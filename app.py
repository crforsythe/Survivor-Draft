"""
Survivor Draft – Task 1: Onboarding & Supabase Connection
==========================================================
Handles:
  • Supabase client setup (cached per session)
  • Castaway display (read-only, from the `castaways` table)
  • Login / Registration via the `users` table
  • Session isolation via st.session_state["username"]

Schema tables used here:
  users      – id, username, total_score, correct_guesses, created_at
  castaways  – id, player_name, status, actual_rank, is_final_three, is_winner
"""

import uuid

import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client, Client


# ── Supabase client (created once, cached for the lifetime of the app) ────────
@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_users() -> list[str]:
    """Return a sorted list of registered usernames from the `users` table."""
    supabase = get_supabase()
    response = supabase.table("users").select("username").execute()
    return sorted(row["username"] for row in response.data if row.get("username"))


def load_castaway_pool() -> pd.DataFrame:
    """Return all rows from the `castaways` table as a DataFrame."""
    supabase = get_supabase()
    response = supabase.table("castaways").select("*").execute()
    df = pd.DataFrame(response.data)
    return df


# Tribe display config
TRIBE_STYLE: dict[str, dict] = {
    "Vatu": {"emoji": "🟣", "color": "#7B2FBE"},
    "Cila": {"emoji": "🟠", "color": "#D95F02"},
    "Kalo": {"emoji": "🟢", "color": "#1B8A2A"},
}


def register_user(new_username: str) -> bool:
    """
    Insert a new username into the `users` table.

    Supabase handles concurrent inserts safely at the database level.
    Add a UNIQUE constraint on the `username` column in your Supabase schema
    to guarantee no duplicates even under race conditions.

    Returns True if inserted, False if the username already exists.
    """
    supabase = get_supabase()

    # Check for existing user (case-insensitive)
    existing = (
        supabase.table("users")
        .select("username")
        .ilike("username", new_username)
        .execute()
    )
    if existing.data:
        return False  # already registered

    supabase.table("users").insert({
        "id": str(uuid.uuid4()),
        "username": new_username,
    }).execute()
    return True


def load_user_predictions(username: str) -> pd.DataFrame:
    """Return every castaway merged with this user's current predicted_rank."""
    supabase = get_supabase()
    cast_resp = supabase.table("castaways").select("player_name, tribe").execute()
    cast_df = pd.DataFrame(cast_resp.data)

    pred_resp = (
        supabase.table("predictions")
        .select("player_name, predicted_rank")
        .eq("username", username)
        .execute()
    )
    pred_df = (
        pd.DataFrame(pred_resp.data)
        if pred_resp.data
        else pd.DataFrame(columns=["player_name", "predicted_rank"])
    )

    merged = cast_df.merge(pred_df, on="player_name", how="left")
    merged["predicted_rank"] = merged["predicted_rank"].astype("Int64")
    return merged.sort_values("player_name").reset_index(drop=True)


def save_user_predictions(username: str, df: pd.DataFrame) -> None:
    """Replace all predictions for this user (delete-then-insert)."""
    supabase = get_supabase()
    supabase.table("predictions").delete().eq("username", username).execute()
    rows = [
        {
            "username": username,
            "player_name": row["player_name"],
            "predicted_rank": int(row["predicted_rank"]),
        }
        for _, row in df.iterrows()
        if pd.notna(row["predicted_rank"])
    ]
    if rows:
        supabase.table("predictions").insert(rows).execute()


def load_all_predictions() -> pd.DataFrame:
    """Pivot table: castaways as rows, users as columns, predicted_rank as values."""
    supabase = get_supabase()

    # All castaways for the index
    cast_resp = supabase.table("castaways").select("player_name, tribe, actual_rank").execute()
    cast_df = pd.DataFrame(cast_resp.data)

    # All predictions across all users
    pred_resp = supabase.table("predictions").select("username, player_name, predicted_rank").execute()
    if not pred_resp.data:
        return cast_df.set_index("player_name")

    pred_df = pd.DataFrame(pred_resp.data)
    pivot = pred_df.pivot(index="player_name", columns="username", values="predicted_rank")
    pivot = pivot.astype("Int64")

    # Join tribe + actual_rank, sort by average predicted rank
    result = cast_df.set_index("player_name").join(pivot, how="left")
    result["actual_rank"] = result["actual_rank"].astype("Int64")
    user_cols = [c for c in result.columns if c not in ("tribe", "actual_rank")]
    result["avg_rank"] = result[user_cols].mean(axis=1)
    result = result.sort_values("avg_rank").drop(columns="avg_rank")
    # Put actual_rank first among data columns
    col_order = ["tribe", "actual_rank"] + user_cols
    return result[col_order]


def calculate_scores() -> pd.DataFrame:
    """
    Compute current scores for every user using the draft scoring rules:

      Base  : min(actual_rank, predicted_rank) per castaway
      Exact : +1 if actual == predicted
      Final3: +3 if user predicted Final 3 AND castaway is actually in Final 3
      Winner: +5 if user predicted Winner AND castaway actually won
              (stacks on top of Final3 bonus)

    Only castaways with an actual_rank set are scored (i.e. already eliminated).
    """
    supabase = get_supabase()

    cast_resp = (
        supabase.table("castaways")
        .select("player_name, actual_rank, is_final_three, is_winner")
        .execute()
    )
    cast_df = pd.DataFrame(cast_resp.data)
    n_players = len(cast_df)
    final_3_min = n_players - 2  # e.g. rank 22 in a 24-player season

    pred_resp = supabase.table("predictions").select("username, player_name, predicted_rank").execute()
    if not pred_resp.data:
        return pd.DataFrame(columns=["Rank", "Player", "Score", "Exact Picks", "Picks Scored"])

    pred_df = pd.DataFrame(pred_resp.data)
    merged = pred_df.merge(cast_df, on="player_name", how="left")

    # Only score castaways that have been eliminated
    scored = merged[merged["actual_rank"].notna()].copy()
    scored["actual_rank"] = scored["actual_rank"].astype(int)
    scored["predicted_rank"] = scored["predicted_rank"].astype(int)

    results = []
    for user, group in scored.groupby("username"):
        total = 0
        exact = 0
        for _, row in group.iterrows():
            actual    = row["actual_rank"]
            predicted = row["predicted_rank"]

            # Base survival score
            total += min(actual, predicted)

            # Exact match bonus
            if actual == predicted:
                total += 1
                exact += 1

            # Final 3 bonus
            in_final_3_actual    = bool(row.get("is_final_three")) or (actual >= final_3_min)
            in_final_3_predicted = predicted >= final_3_min
            if in_final_3_actual and in_final_3_predicted:
                total += 3

            # Winner bonus (stacks with Final 3)
            actually_won       = bool(row.get("is_winner")) or (actual == n_players)
            predicted_winner   = predicted == n_players
            if actually_won and predicted_winner:
                total += 5

        results.append({
            "Player":       user,
            "Score":        total,
            "Exact Picks":  exact,
            "Picks Scored": len(group),
        })

    if not results:
        return pd.DataFrame(columns=["Rank", "Player", "Score", "Exact Picks", "Picks Scored"])

    df = pd.DataFrame(results).sort_values("Score", ascending=False).reset_index(drop=True)
    df.insert(0, "Rank", df.index + 1)
    return df


def get_game_state() -> dict:
    """
    Returns a snapshot of the current game useful for generating narrative text:
      - n_eliminated, n_remaining
      - most_recent: dict with castaway info for the last voted-out player
      - for the most recent elimination: who predicted them in that exact spot,
        who had them surviving longer, who had them going earlier
      - current leader(s) and gap to second place
    """
    supabase = get_supabase()
    cast_resp = supabase.table("castaways").select("*").execute()
    cast_df = pd.DataFrame(cast_resp.data)

    eliminated = cast_df[cast_df["actual_rank"].notna()].copy()
    eliminated["actual_rank"] = eliminated["actual_rank"].astype(int)

    if eliminated.empty:
        return {"n_eliminated": 0, "n_remaining": len(cast_df), "most_recent": None}

    most_recent = eliminated.sort_values("actual_rank").iloc[-1].to_dict()
    n_elim = len(eliminated)
    n_rem  = len(cast_df) - n_elim

    # Load predictions for the most recently eliminated castaway
    pred_resp = (
        supabase.table("predictions")
        .select("username, predicted_rank")
        .eq("player_name", most_recent["player_name"])
        .execute()
    )
    preds = pd.DataFrame(pred_resp.data) if pred_resp.data else pd.DataFrame()

    actual_r = int(most_recent["actual_rank"])
    exact, too_high, too_low = [], [], []
    if not preds.empty:
        for _, row in preds.iterrows():
            pr = int(row["predicted_rank"])
            if pr == actual_r:
                exact.append(row["username"])
            elif pr > actual_r:
                too_high.append(row["username"])   # overestimated survival
            else:
                too_low.append(row["username"])    # underestimated survival

    # Final 3 / Winner info
    final_three = cast_df[cast_df["is_final_three"] == True]["player_name"].tolist()
    winner_rows  = cast_df[cast_df["is_winner"] == True]["player_name"].tolist()
    # Fallback: if nobody has is_winner set but someone has the max actual_rank, treat them as winner
    if not winner_rows and not eliminated.empty:
        n_total = len(cast_df)
        top = eliminated[eliminated["actual_rank"] == n_total]
        winner_rows = top["player_name"].tolist()
    winner = winner_rows[0] if winner_rows else None

    # Scores for gap calculation
    scores = calculate_scores()

    return {
        "n_eliminated":  n_elim,
        "n_remaining":   n_rem,
        "most_recent":   most_recent,
        "exact":         exact,
        "too_high":      too_high,
        "too_low":       too_low,
        "final_three":   final_three,
        "winner":        winner,
        "scores":        scores,
    }


def compute_score_progression() -> pd.DataFrame:
    """
    Cumulative score per user at each elimination checkpoint.
    Returns a long-form DataFrame: [elimination, username, cumulative_score].
    """
    supabase = get_supabase()
    cast_resp = supabase.table("castaways").select("player_name, actual_rank, is_final_three, is_winner").execute()
    cast_df = pd.DataFrame(cast_resp.data)
    n_players = len(cast_df)
    final_3_min = n_players - 2

    eliminated = cast_df[cast_df["actual_rank"].notna()].copy()
    eliminated["actual_rank"] = eliminated["actual_rank"].astype(int)
    if eliminated.empty:
        return pd.DataFrame(columns=["Elimination", "Player", "Score"])

    pred_resp = supabase.table("predictions").select("username, player_name, predicted_rank").execute()
    if not pred_resp.data:
        return pd.DataFrame(columns=["Elimination", "Player", "Score"])

    pred_df = pd.DataFrame(pred_resp.data)
    merged = pred_df.merge(cast_df, on="player_name", how="left")
    scored = merged[merged["actual_rank"].notna()].copy()
    scored["actual_rank"]    = scored["actual_rank"].astype(int)
    scored["predicted_rank"] = scored["predicted_rank"].astype(int)
    elim_order = sorted(scored["actual_rank"].unique())

    rows = []
    for user, grp in scored.groupby("username"):
        running = 0
        grp_indexed = grp.set_index("actual_rank")
        for rank in elim_order:
            if rank in grp_indexed.index:
                row = grp_indexed.loc[rank]
                actual    = rank                          # rank IS the actual_rank (it's the index now)
                predicted = int(row["predicted_rank"])
                running += min(actual, predicted)
                if actual == predicted:
                    running += 1
                in_f3_actual    = bool(row.get("is_final_three")) or actual >= final_3_min
                in_f3_predicted = predicted >= final_3_min
                if in_f3_actual and in_f3_predicted:
                    running += 3
                if (bool(row.get("is_winner")) or actual == n_players) and predicted == n_players:
                    running += 5
            rows.append({"Elimination": rank, "Player": user, "Score": running})

    return pd.DataFrame(rows)




# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Survivor Draft",
    page_icon="🏝️",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────────────────────
# UI – Header
# ─────────────────────────────────────────────────────────────────────────────

st.title("🏝️ Survivor Draft")
st.caption("Pick your castaways. Outlast. Outwit. Outdraft.")

with st.expander("ℹ️ How to Play", expanded=False):
    st.markdown("""
**Welcome to the Survivor 50 Draft!** Here's everything you need to know:

---

#### 🔐 Step 1 — Log in or Register
- If you've played before, **select your name** from the dropdown and click **Log in**.
- New? Type your name in the **Register** field and click **Register & Log in**. Your account is created instantly.

---

#### 🏝️ Step 2 — Browse the Cast
- Head to the **Cast** tab to see all 24 Season 50 castaways.
- Use the **tribe filter** to view Cila 🟠, Kalo 🟢, or Vatu 🟣 separately.
- Each card shows the castaway's seasons played, age, hometown, and occupation.

---

#### 📊 Step 3 — Set Your Elimination Order
- Go to the **My Picks** tab.
- Assign a **rank to every castaway** in the "Your Rank" column:
  - **Rank 1** = the person you think gets voted out first.
  - **Rank 24** = the person you think wins (Sole Survivor).
- You can save a partial list and come back — just hit **💾 Save My Picks** anytime.
- Each number can only be used once. Duplicates are blocked on save.

---

#### 📋 Step 4 — Check the Overview
- The **Overview** tab is the game hub. It shows:
  - A live **narrative** updating after each elimination.
  - **🏆 Current Standings** bar chart and a **📈 Score Progression** line chart.
  - An expandable **pick-by-pick breakdown** comparing everyone's predictions.

---

#### 🧮 Scoring
| Situation | Points |
|---|---|
| Base score per castaway | `min(your predicted rank, actual rank)` |
| Exact rank prediction | +1 bonus |
| Correctly predicted Final 3 | +3 bonus |
| Correctly predicted the Winner | +5 bonus (stacks with Final 3) |

Scores update automatically after each episode once the show admin enters the latest elimination in the database.
""")





# ─────────────────────────────────────────────────────────────────────────────
# UI – Login / Registration
# ─────────────────────────────────────────────────────────────────────────────

if "username" in st.session_state and st.session_state["username"]:
    username = st.session_state["username"]

    st.sidebar.success(f"Logged in as **{username}**")
    if st.sidebar.button("Log out"):
        del st.session_state["username"]
        st.rerun()

    st.success(f"Welcome, **{username}**! 🎉")
    st.divider()

    tab_cast, tab_picks, tab_overview = st.tabs(["\U0001f3dd\ufe0f Cast", "\U0001f4ca My Picks", "\U0001f4cb Overview"])

    # ── Tab 1: Castaway Browser ───────────────────────────────────────────
    with tab_cast:
        st.subheader("Season 50 Cast")
        with st.spinner("Loading cast…"):
            try:
                cast_df = load_castaway_pool()
            except Exception as e:
                st.error(f"Could not load castaways: {e}")
                cast_df = pd.DataFrame()

        if not cast_df.empty:
            tribes = ["All"] + sorted(cast_df["tribe"].dropna().unique().tolist()) if "tribe" in cast_df.columns else ["All"]
            selected_tribe = st.radio(
                "Filter by tribe", options=tribes, horizontal=True, label_visibility="collapsed",
            )
            filtered = cast_df if selected_tribe == "All" else cast_df[cast_df["tribe"] == selected_tribe]
            filtered = filtered.sort_values("player_name").reset_index(drop=True)

            cols = st.columns(3, gap="medium")
            for i, row in filtered.iterrows():
                with cols[i % 3]:
                    with st.container(border=True):
                        photo = row.get("photo_url") if "photo_url" in row else None
                        if photo and isinstance(photo, str) and photo.startswith("http"):
                            st.image(photo, use_container_width=True)
                        else:
                            st.markdown("<div style='text-align:center;font-size:3rem;padding:0.5rem'>🧑\u200d🌴</div>", unsafe_allow_html=True)
                        st.markdown(f"**{row['player_name']}**")
                        tribe = row.get("tribe", "")
                        style = TRIBE_STYLE.get(tribe, {"emoji": "⚪", "color": "#888"})
                        st.markdown(
                            f"<span style='background:{style['color']};color:white;"
                            f"padding:2px 10px;border-radius:12px;font-size:0.75rem;font-weight:600'>"
                            f"{style['emoji']} {tribe}</span>",
                            unsafe_allow_html=True,
                        )
                        details = []
                        if row.get("seasons_played"): details.append(f"📺 {row['seasons_played']}")
                        if row.get("age"):            details.append(f"🎂 Age {int(row['age'])}")
                        if row.get("hometown"):       details.append(f"📍 {row['hometown']}")
                        if row.get("occupation"):    details.append(f"💼 {row['occupation']}")
                        if details:
                            st.caption("  \n".join(details))

    # ── Tab 2: My Picks ───────────────────────────────────────────────
    with tab_picks:
        st.subheader("My Elimination Order")
        st.caption(
            "Rank every castaway from **1** (first boot) to **24** (Sole Survivor). "
            "All 24 must be filled in and each number can only be used once."
        )
        st.info("👆 **Double-click** any cell in the **Your Rank** column to enter or change a number, then click **💾 Save My Picks** when you're done.")

        with st.spinner("Loading your picks…"):
            try:
                picks_df = load_user_predictions(username)
            except Exception as e:
                st.error(f"Could not load predictions: {e}")
                picks_df = pd.DataFrame()

        if not picks_df.empty:
            n_players = len(picks_df)
            edited = st.data_editor(
                picks_df[["player_name", "tribe", "predicted_rank"]],
                column_config={
                    "player_name": st.column_config.TextColumn("Castaway", disabled=True),
                    "tribe":       st.column_config.TextColumn("Tribe", disabled=True),
                    "predicted_rank": st.column_config.NumberColumn(
                        "Your Rank",
                        help="1 = first voted out  ·  24 = Sole Survivor",
                        min_value=1,
                        max_value=n_players,
                        step=1,
                    ),
                },
                hide_index=True,
                use_container_width=True,
                key="picks_editor",
            )

            ranks_filled = edited["predicted_rank"].dropna()
            n_filled = len(ranks_filled)
            n_total = n_players

            if st.button("💾 Save My Picks", type="primary", use_container_width=True):
                if ranks_filled.nunique() < n_filled:
                    dupes = sorted(ranks_filled[ranks_filled.duplicated()].astype(int).tolist())
                    st.warning(f"Duplicate rank(s): {dupes}. Each number must be used exactly once.")
                else:
                    with st.spinner("Saving…"):
                        try:
                            save_user_predictions(username, edited)
                            saved_msg = f"✅ {n_filled}/{n_total} picks saved."
                            if n_filled < n_total:
                                saved_msg += f" ({n_total - n_filled} castaways still unranked.)"
                            st.success(saved_msg)
                        except Exception as e:
                            st.error(f"Save failed: {e}")

    with tab_overview:
        st.subheader("Overview")

        with st.spinner("Loading game state…"):
            try:
                state = get_game_state()
                scores_df = state.get("scores", pd.DataFrame())
                prog_df   = compute_score_progression()
                overview_df = load_all_predictions()
            except Exception as e:
                st.error(f"Could not load overview: {e}")
                state = {}
                scores_df = prog_df = overview_df = pd.DataFrame()

        # ── Narrative ─────────────────────────────────────────────────────────
        n_elim = state.get("n_eliminated", 0)
        n_rem  = state.get("n_remaining",  24)
        recent = state.get("most_recent")

        if n_elim == 0:
            st.info("⏳ The game hasn't started yet — no castaways have been eliminated. Come back after the first episode!")
        else:
            exact    = state.get("exact", [])
            too_high = state.get("too_high", [])
            too_low  = state.get("too_low", [])
            name     = recent["player_name"]
            actual_r = int(recent["actual_rank"])

            winner      = state.get("winner")
            final_three = state.get("final_three", [])

            # Narrative block — winner announcement takes precedence over boot framing
            if winner:
                narrative_lines = [
                    f"**🔴 Previously on Survivor 50…**  \n",
                    f"The game is over! All {n_elim} castaways have been accounted for.",
                    "",
                    f"👑 **{winner} has been crowned the Sole Survivor of Season 50!**",
                ]
            else:
                narrative_lines = [
                    f"**🔴 Previously on Survivor 50…**  \n",
                    f"{n_elim} castaway{'s have' if n_elim > 1 else ' has'} been eliminated — "
                    f"**{n_rem}** remain{'s' if n_rem == 1 else ''} in the game.",
                    "",
                    f"**Most recently voted out:** {name} (rank #{actual_r})",
                ]

            if exact:
                preds = ", ".join(exact)
                narrative_lines.append(f"✅ {preds} correctly predicted {name} would be eliminated #{actual_r}!")
            else:
                narrative_lines.append(f"Nobody predicted {name} would go at #{actual_r} — no exact match bonuses this round.")

            if not winner and len(final_three) >= 3:
                f3_names = ", ".join(final_three)
                narrative_lines.append(f"🔥 **We have a Final Three!** {f3_names} are heading to Final Tribal Council.")


            if not scores_df.empty and len(scores_df) >= 2:
                leader = scores_df.iloc[0]
                second = scores_df.iloc[1]
                gap    = int(leader["Score"]) - int(second["Score"])
                if gap == 0:
                    narrative_lines.append(f"🔥 **It's tied at the top!** {leader['Player']} and {second['Player']} are level on {int(leader['Score'])} pts.")
                else:
                    narrative_lines.append(f"🏅 **{leader['Player']} leads** with {int(leader['Score'])} pts — {gap} ahead of {second['Player']}.")

            st.markdown("  \n".join(narrative_lines))

        st.divider()

        # ── Charts ────────────────────────────────────────────────────────────
        if not scores_df.empty:
            col_bar, col_line = st.columns(2, gap="large")

            with col_bar:
                st.markdown("#### 🏆 Current Standings")
                bar_df = scores_df.sort_values("Score")
                fig_bar = px.bar(
                    bar_df,
                    x="Score",
                    y="Player",
                    orientation="h",
                    color="Player",
                    text="Score",
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                fig_bar.update_traces(textposition="outside")
                fig_bar.update_layout(
                    showlegend=False,
                    margin=dict(l=0, r=30, t=10, b=0),
                    xaxis_title="",
                    yaxis_title="",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color="#fafafa",
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with col_line:
                st.markdown("#### 📈 Score Progression")
                if not prog_df.empty:
                    fig_line = px.line(
                        prog_df,
                        x="Elimination",
                        y="Score",
                        color="Player",
                        markers=True,
                        color_discrete_sequence=px.colors.qualitative.Bold,
                    )
                    fig_line.update_layout(
                        margin=dict(l=0, r=10, t=10, b=0),
                        xaxis_title="Castaway Eliminated (rank #)",
                        yaxis_title="Cumulative Score",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font_color="#fafafa",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
                    st.plotly_chart(fig_line, use_container_width=True)
                else:
                    st.caption("Score progression will appear after the first elimination.")

            st.divider()

        # ── Pick-by-pick table ────────────────────────────────────────────────
        with st.expander("📋 Pick-by-pick breakdown", expanded=False):
            if overview_df.empty:
                st.info("No picks submitted yet.")
            else:
                user_cols = [c for c in overview_df.columns if c not in ("tribe", "actual_rank")]
                tribe_colours = {"Vatu": "#EDE7F6", "Cila": "#FFF3E0", "Kalo": "#E8F5E9"}

                def colour_tribe_row(row):
                    bg = tribe_colours.get(row.get("tribe", ""), "")
                    return [f"background-color:{bg}" if bg else "" for _ in row]

                display_df = overview_df.reset_index()
                fmt = {c: lambda x: str(int(x)) if pd.notna(x) else "—" for c in user_cols}
                fmt["actual_rank"] = lambda x: str(int(x)) if pd.notna(x) else "—"
                styled = display_df.style.apply(colour_tribe_row, axis=1).format(fmt)
                st.dataframe(
                    styled,
                    use_container_width=True,
                    hide_index=True,
                    column_config={"actual_rank": st.column_config.NumberColumn("✅ Actual")},
                )

else:
    # ── Load current user list ────────────────────────────────────────────────
    with st.spinner("Loading users…"):
        try:
            user_list = load_users()
        except Exception as e:
            st.error(f"Could not load users: {e}")
            st.stop()

    st.subheader("🔐 Login or Register")

    col_login, col_register = st.columns(2, gap="large")

    # ── Existing user login ───────────────────────────────────────────────────
    with col_login:
        st.markdown("**Select your name**")
        if user_list:
            selected = st.selectbox(
                "Choose your username",
                options=["— select —"] + user_list,
                key="login_select",
                label_visibility="collapsed",
            )
            if st.button("Log in", use_container_width=True, type="primary"):
                if selected == "— select —":
                    st.warning("Please choose a username from the list.")
                else:
                    st.session_state["username"] = selected
                    st.rerun()
        else:
            st.info("No users yet – register below to be the first!")

    # ── New user registration ─────────────────────────────────────────────────
    with col_register:
        st.markdown("**Register a new name**")
        new_name = st.text_input(
            "New username",
            placeholder="Enter your name…",
            key="register_input",
            label_visibility="collapsed",
        )
        if st.button("Register & Log in", use_container_width=True):
            clean_name = new_name.strip()
            if not clean_name:
                st.warning("Please enter a username.")
            elif len(clean_name) > 50:
                st.warning("Username must be 50 characters or fewer.")
            else:
                with st.spinner("Registering…"):
                    try:
                        added = register_user(clean_name)
                        if added:
                            st.success(f"Registered as **{clean_name}**!")
                        else:
                            st.info(f"**{clean_name}** already exists – logging you in.")
                        st.session_state["username"] = clean_name
                        st.rerun()
                    except Exception as e:
                        st.error(f"Registration failed: {e}")
