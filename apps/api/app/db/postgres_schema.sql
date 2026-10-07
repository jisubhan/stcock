-- Optional production migration. Local demo uses SQLite with zero service prerequisites.
CREATE TABLE IF NOT EXISTS stock_master (
    symbol text PRIMARY KEY, name text NOT NULL, market text NOT NULL,
    currency text DEFAULT 'KRW', active boolean DEFAULT true, updated_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS candles_1m (
    symbol text REFERENCES stock_master(symbol), timestamp timestamptz, open numeric NOT NULL,
    high numeric NOT NULL, low numeric NOT NULL, close numeric NOT NULL, volume bigint NOT NULL,
    venue text, PRIMARY KEY(symbol,timestamp)
);
CREATE TABLE IF NOT EXISTS candles_1d (
    symbol text REFERENCES stock_master(symbol), date date, open numeric NOT NULL,
    high numeric NOT NULL, low numeric NOT NULL, close numeric NOT NULL, volume bigint NOT NULL,
    integrated boolean NOT NULL DEFAULT false, PRIMARY KEY(symbol,date)
);
CREATE TABLE IF NOT EXISTS investor_flow (
    symbol text REFERENCES stock_master(symbol), date date, retail_net numeric,
    foreign_net numeric, institution_net numeric, other_net numeric, PRIMARY KEY(symbol,date)
);
CREATE TABLE IF NOT EXISTS program_flow (
    symbol text REFERENCES stock_master(symbol), date date, arbitrage_net numeric,
    non_arbitrage_net numeric, total_net numeric, PRIMARY KEY(symbol,date)
);
CREATE TABLE IF NOT EXISTS theme (id text PRIMARY KEY, name text NOT NULL);
CREATE TABLE IF NOT EXISTS theme_stock (
    theme_id text REFERENCES theme(id), symbol text REFERENCES stock_master(symbol),
    weight numeric DEFAULT 1, PRIMARY KEY(theme_id,symbol)
);
CREATE TABLE IF NOT EXISTS theme_us_mapping (
    theme_id text REFERENCES theme(id), ticker text, weight numeric DEFAULT 1, PRIMARY KEY(theme_id,ticker)
);
CREATE TABLE IF NOT EXISTS signals (
    id bigserial PRIMARY KEY, symbol text REFERENCES stock_master(symbol), timestamp timestamptz NOT NULL,
    signal_type text NOT NULL, score numeric NOT NULL, current_price numeric NOT NULL,
    entry_low numeric NOT NULL, entry_high numeric NOT NULL, stop_price numeric NOT NULL,
    target1 numeric NOT NULL, target2 numeric NOT NULL, reason_json jsonb NOT NULL, status text NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_backtest (
    signal_id bigint REFERENCES signals(id), entry_price numeric, max_favorable_excursion numeric,
    max_adverse_excursion numeric, target_hit boolean, stop_hit boolean, result_pct numeric
);
