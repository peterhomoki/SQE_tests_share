-- SQE1 question bank schema
-- Normalised: m:n questions↔topics, one row per option,
-- composite FK guarantees a question's topics share its FLK.

PRAGMA foreign_keys = ON;

-- ─────────────────────────────────────────────────────────────
-- topics: four-level taxonomy as a self-referencing tree
--   level 1 = area      (BLP, PLP …)        parent_id NULL, code NOT NULL
--   level 2 = field
--   level 3 = subject matter
--   level 4 = sub-matter
-- ─────────────────────────────────────────────────────────────
CREATE TABLE topics (
    topic_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id  INTEGER REFERENCES topics(topic_id) ON DELETE CASCADE,
    level      INTEGER NOT NULL CHECK (level IN (1,2,3,4)),
    code       TEXT,
    name       TEXT NOT NULL,
    flk        TEXT NOT NULL CHECK (flk IN ('FLK1','FLK2')),
    UNIQUE (parent_id, name),
    -- composite uniqueness lets question_topics enforce flk consistency
    UNIQUE (topic_id, flk),
    CHECK (
        (level = 1 AND parent_id IS NULL  AND code IS NOT NULL) OR
        (level > 1 AND parent_id IS NOT NULL AND code IS NULL)
    )
);
CREATE INDEX idx_topics_parent ON topics(parent_id);
CREATE INDEX idx_topics_flk    ON topics(flk);

-- ─────────────────────────────────────────────────────────────
-- questions: one per imported MCQ
-- ─────────────────────────────────────────────────────────────
CREATE TABLE questions (
    question_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flk                  TEXT    NOT NULL CHECK (flk IN ('FLK1','FLK2')),
    source_file          TEXT    NOT NULL,
    source_question_num  INTEGER NOT NULL,
    stem                 TEXT    NOT NULL,
    lead_in              TEXT    NOT NULL,
    correct_rate_pct     INTEGER,                    -- when published by SRA
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_file, source_question_num),
    -- composite uniqueness lets question_topics enforce flk consistency
    UNIQUE (question_id, flk)
);
CREATE INDEX idx_questions_flk ON questions(flk);

-- ─────────────────────────────────────────────────────────────
-- options: one row per A–E for each question
--   is_correct + partial unique index ⇒ exactly one correct option / question
--   explanation is nullable; populated later from the explanations file
-- ─────────────────────────────────────────────────────────────
CREATE TABLE options (
    option_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id  INTEGER NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    label        TEXT    NOT NULL CHECK (label IN ('A','B','C','D','E')),
    text         TEXT    NOT NULL,
    is_correct   INTEGER NOT NULL DEFAULT 0 CHECK (is_correct IN (0,1)),
    explanation  TEXT,
    UNIQUE (question_id, label)
);
CREATE INDEX idx_options_question ON options(question_id);
CREATE UNIQUE INDEX idx_one_correct_per_question
    ON options(question_id) WHERE is_correct = 1;

-- ─────────────────────────────────────────────────────────────
-- question_topics: m:n junction. flk column + composite FKs
-- guarantee that every linked topic shares the question's FLK,
-- so a question is always FLK1-only or FLK2-only.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE question_topics (
    question_id  INTEGER NOT NULL,
    topic_id     INTEGER NOT NULL,
    flk          TEXT    NOT NULL CHECK (flk IN ('FLK1','FLK2')),
    reasoning    TEXT    CHECK (reasoning IS NULL OR length(reasoning) <= 1000),
    PRIMARY KEY (question_id, topic_id),
    FOREIGN KEY (question_id, flk) REFERENCES questions(question_id, flk) ON DELETE CASCADE,
    FOREIGN KEY (topic_id,    flk) REFERENCES topics(topic_id,        flk)
);
CREATE INDEX idx_qt_topic ON question_topics(topic_id);

-- ─────────────────────────────────────────────────────────────
-- convenience view: flattened question + correct option
-- ─────────────────────────────────────────────────────────────
CREATE VIEW v_questions AS
SELECT
    q.question_id,
    q.flk,
    q.source_file,
    q.source_question_num,
    q.stem,
    q.lead_in,
    o.label   AS correct_label,
    o.text    AS correct_text,
    q.correct_rate_pct
FROM questions q
LEFT JOIN options o
       ON o.question_id = q.question_id AND o.is_correct = 1;
