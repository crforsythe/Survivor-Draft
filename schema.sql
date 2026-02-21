-- 1. Create the Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT auth.uid(), -- Optional: if you ever want real auth
    username TEXT UNIQUE NOT NULL,
    total_score INTEGER DEFAULT 0,
    correct_guesses INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Create the Castaways table (The master list for the season)
CREATE TABLE castaways (
    id SERIAL PRIMARY KEY,
    player_name TEXT UNIQUE NOT NULL,
    status TEXT DEFAULT 'Active', -- 'Active' or 'Voted Out'
    actual_rank INTEGER, -- To be filled as they are eliminated (1, 2, 3...)
    is_final_three BOOLEAN DEFAULT FALSE,
    is_winner BOOLEAN DEFAULT FALSE
);

-- 3. Create the Predictions table (The "Draft" picks)
CREATE TABLE predictions (
    id SERIAL PRIMARY KEY,
    username TEXT REFERENCES users(username) ON UPDATE CASCADE,
    player_name TEXT REFERENCES castaways(player_name) ON UPDATE CASCADE,
    predicted_rank INTEGER NOT NULL,
    UNIQUE(username, player_name), -- Prevents a user from ranking the same player twice
    UNIQUE(username, predicted_rank) -- Prevents a user from having two '1st place' picks
);