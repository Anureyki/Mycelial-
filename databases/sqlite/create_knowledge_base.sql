CREATE TABLE IF NOT EXISTS knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT,
    source_url TEXT,
    content_type TEXT, -- 'pdf', 'html', 'text'
    summary TEXT,
    key_findings TEXT,
    processed_by TEXT, -- 'lstm', 'cnn', 'transformer', 'rnn'
    processed_date DATE,
    trusted BOOLEAN DEFAULT 1
);
