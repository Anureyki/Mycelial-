CREATE TABLE trusted_sources (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    category TEXT NOT NULL, -- 'legal', 'research', 'government', 'scientific'
    last_verified DATE,
    active BOOLEAN DEFAULT 1
);

-- Insert known trusted sources
INSERT INTO trusted_sources (name, url, category) VALUES
('Cornell LII (Legal)', 'https://www.law.cornell.edu', 'legal'),
('USDA', 'https://www.usda.gov', 'government'),
('EPA', 'https://www.epa.gov', 'government'),
('PubMed', 'https://pubmed.ncbi.nlm.nih.gov', 'scientific'),
('arXiv', 'https://arxiv.org', 'scientific'),
('Google Scholar', 'https://scholar.google.com', 'research');
