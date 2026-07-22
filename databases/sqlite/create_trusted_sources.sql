-- Trusted Sources Database
-- Categories: legal, government, agricultural, defense, scientific, research

CREATE TABLE IF NOT EXISTS trusted_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    description TEXT,
    last_verified DATE,
    active BOOLEAN DEFAULT 1
);

-- Clear existing data (if any)
DELETE FROM trusted_sources;

-- ============================================
-- LEGAL
-- ============================================
INSERT INTO trusted_sources (name, url, category, subcategory, description) VALUES
('Cornell LII (Legal Information Institute)', 'https://www.law.cornell.edu', 'legal', 'general', 'US law, statutes, and case law'),
('Supreme Court of the United States', 'https://www.supremecourt.gov', 'legal', 'court', 'SCOTUS opinions and orders'),
('Federal Register', 'https://www.federalregister.gov', 'legal', 'regulations', 'Daily US federal regulations and notices'),
('Code of Federal Regulations (CFR)', 'https://www.ecfr.gov', 'legal', 'regulations', 'Official CFR publications'),
('U.S. Congress', 'https://www.congress.gov', 'legal', 'legislation', 'Bills, laws, and legislative information'),
('U.S. Courts', 'https://www.uscourts.gov', 'legal', 'court', 'Federal court system information');

-- ============================================
-- GOVERNMENT
-- ============================================
INSERT INTO trusted_sources (name, url, category, subcategory, description) VALUES
('USA.gov', 'https://www.usa.gov', 'government', 'general', 'Official US government portal'),
('Data.gov', 'https://www.data.gov', 'government', 'data', 'US government open data portal'),
('GPO (Government Publishing Office)', 'https://www.gpo.gov', 'government', 'publishing', 'Official government publications'),
('GAO (Government Accountability Office)', 'https://www.gao.gov', 'government', 'audit', 'Audits and evaluations');

-- ============================================
-- AGRICULTURAL
-- ============================================
INSERT INTO trusted_sources (name, url, category, subcategory, description) VALUES
('USDA (United States Department of Agriculture)', 'https://www.usda.gov', 'agricultural', 'general', 'USDA main portal'),
('USDA NASS (National Agricultural Statistics Service)', 'https://www.nass.usda.gov', 'agricultural', 'data', 'Agricultural statistics'),
('USDA APHIS (Animal and Plant Health Inspection Service)', 'https://www.aphis.usda.gov', 'agricultural', 'regulations', 'Plant health, contamination, and quarantine'),
('USDA NRCS (Natural Resources Conservation Service)', 'https://www.nrcs.usda.gov', 'agricultural', 'conservation', 'Soil and water conservation'),
('USDA AMS (Agricultural Marketing Service)', 'https://www.ams.usda.gov', 'agricultural', 'commerce', 'Marketing and grading standards'),
('USDA ERS (Economic Research Service)', 'https://www.ers.usda.gov', 'agricultural', 'research', 'Agricultural economics and research'),
('USDA Agricultural Research Service (ARS)', 'https://www.ars.usda.gov', 'agricultural', 'research', 'Scientific research on agriculture');

-- ============================================
-- DEFENSE (DOD)
-- ============================================
INSERT INTO trusted_sources (name, url, category, subcategory, description) VALUES
('DoD (Department of Defense)', 'https://www.defense.gov', 'defense', 'general', 'Official DoD portal'),
('DoD Directives & Instructions', 'https://www.esd.whs.mil/dd', 'defense', 'regulations', 'DoD issuances, directives, and instructions'),
('Defense Technical Information Center (DTIC)', 'https://www.dtic.mil', 'defense', 'research', 'DoD scientific and technical research'),
('National Defense Authorization Act (NDAA)', 'https://www.congress.gov/ndaa', 'defense', 'legislation', 'Annual defense authorization'),
('U.S. Army', 'https://www.army.mil', 'defense', 'service', 'Official U.S. Army site'),
('U.S. Navy', 'https://www.navy.mil', 'defense', 'service', 'Official U.S. Navy site'),
('U.S. Air Force', 'https://www.af.mil', 'defense', 'service', 'Official U.S. Air Force site'),
('U.S. Marine Corps', 'https://www.marines.mil', 'defense', 'service', 'Official U.S. Marine Corps site');

-- ============================================
-- SCIENTIFIC
-- ============================================
INSERT INTO trusted_sources (name, url, category, subcategory, description) VALUES
('PubMed', 'https://pubmed.ncbi.nlm.nih.gov', 'scientific', 'medical', 'Biomedical literature'),
('arXiv', 'https://arxiv.org', 'scientific', 'research', 'Preprints in physics, math, computer science'),
('Google Scholar', 'https://scholar.google.com', 'scientific', 'research', 'Scholarly literature search'),
('IEEE Xplore', 'https://ieeexplore.ieee.org', 'scientific', 'engineering', 'Technical and engineering research'),
('Science.gov', 'https://www.science.gov', 'scientific', 'government', 'US government science portal'),
('NASA', 'https://www.nasa.gov', 'scientific', 'space', 'Space and aeronautics research');

-- ============================================
-- RESEARCH & POLICY
-- ============================================
INSERT INTO trusted_sources (name, url, category, subcategory, description) VALUES
('RAND Corporation', 'https://www.rand.org', 'research', 'policy', 'Policy and strategy research'),
('Brookings Institution', 'https://www.brookings.edu', 'research', 'policy', 'Public policy and economics'),
('Pew Research Center', 'https://www.pewresearch.org', 'research', 'policy', 'Social and demographic research'),
('National Academies', 'https://www.nationalacademies.org', 'research', 'policy', 'Scientific and medical policy reports');
