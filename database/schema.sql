-- Схема под app/models.py (initdb и scripts/setup_database.py).
-- При повторном применении к непустой БД удалит перечисленные таблицы (данные пропадут).

DROP TABLE IF EXISTS can_signals CASCADE;
DROP TABLE IF EXISTS can_raw CASCADE;
DROP TABLE IF EXISTS decode_errors CASCADE;
DROP TABLE IF EXISTS telemetry_flat CASCADE;
DROP TABLE IF EXISTS raw_frames CASCADE;
DROP TABLE IF EXISTS can_data CASCADE;
DROP TABLE IF EXISTS positions CASCADE;
DROP TABLE IF EXISTS devices CASCADE;

CREATE TABLE raw_frames (
    id SERIAL PRIMARY KEY,
    payload BYTEA NOT NULL,
    remote_ip VARCHAR(45),
    remote_port INTEGER,
    device_hint VARCHAR(255),
    transport VARCHAR(20) NOT NULL DEFAULT 'tcp',
    received_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE telemetry_flat (
    id SERIAL PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES raw_frames(id) ON DELETE CASCADE,
    device_id VARCHAR(255) NOT NULL,
    device_time TIMESTAMP WITH TIME ZONE,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    course DOUBLE PRECISION,
    ignition BOOLEAN,
    fuel_level DOUBLE PRECISION,
    engine_hours DOUBLE PRECISION,
    temperature DOUBLE PRECISION,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE decode_errors (
    id SERIAL PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES raw_frames(id) ON DELETE CASCADE,
    stage VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE can_raw (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(255) NOT NULL,
    can_id INTEGER NOT NULL,
    payload_hex BYTEA,
    dlc INTEGER NOT NULL,
    is_extended BOOLEAN NOT NULL DEFAULT FALSE,
    dev_time TIMESTAMP WITH TIME ZONE,
    can_channel INTEGER DEFAULT 0,
    rssi INTEGER,
    seq INTEGER,
    src_ip VARCHAR(45),
    raw_id INTEGER REFERENCES raw_frames(id) ON DELETE SET NULL,
    recv_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE can_signals (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(255) NOT NULL,
    signal_time TIMESTAMP WITH TIME ZONE NOT NULL,
    name VARCHAR(255) NOT NULL,
    value_num DOUBLE PRECISION,
    value_text TEXT,
    unit VARCHAR(64),
    src_addr INTEGER,
    pgn INTEGER,
    spn INTEGER,
    mode INTEGER,
    pid INTEGER,
    dict_version VARCHAR(64),
    raw_id INTEGER REFERENCES raw_frames(id) ON DELETE SET NULL
);

CREATE INDEX idx_raw_frames_received_at ON raw_frames(received_at);
CREATE INDEX idx_telemetry_flat_device_id ON telemetry_flat(device_id);
CREATE INDEX idx_can_raw_device_id ON can_raw(device_id);
CREATE INDEX idx_can_raw_recv_time ON can_raw(recv_time);
CREATE INDEX idx_can_signals_device_id ON can_signals(device_id);
CREATE INDEX idx_can_signals_signal_time ON can_signals(signal_time);
