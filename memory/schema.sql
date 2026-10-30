-- 大肥鱼 记忆库 schema v0  (SQLite 3.35+)
-- 原则：一切经历先落库；检索靠打分；不删只衰减。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- 情景记忆流
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,                 -- ISO8601，本地时区
    channel       TEXT    NOT NULL,                 -- wechat / bilibili / dsh / self
    scene         TEXT    NOT NULL,                 -- 群id / 私聊id / 'bili:feed' / 'inner'
    actor_id      TEXT,                             -- 说话人或来源 id
    actor_name    TEXT,
    kind          TEXT    NOT NULL,                 -- message|observation|action|thought|reflection
    text          TEXT    NOT NULL,
    importance    INTEGER NOT NULL DEFAULT 3,       -- 1..10，由小模型打分
    valence       REAL    NOT NULL DEFAULT 0.0,     -- -1..1 情绪色彩
    embedding     BLOB,                             -- float32 little-endian
    source_ref    TEXT,                             -- 原始消息 id / 视频 bvid / url
    recalled_at   TEXT,                             -- 最近一次被检索到
    recall_count  INTEGER NOT NULL DEFAULT 0,
    archived      INTEGER NOT NULL DEFAULT 0        -- 衰减后归档，不删除
);
CREATE INDEX IF NOT EXISTS idx_events_scene_ts ON events(scene, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind     ON events(kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_imp      ON events(importance DESC);

-- 关键词召回：中文按 bigram 预分词后写入 text_seg，用 unicode61 检索。
-- （trigram 分词要求 >=3 字，中文两字词"考研""上班"会召回失败，故不用。）
-- 写入由 memory/store.py 负责，不用触发器，保证与 events 同步。
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    text_seg, tokenize='unicode61 remove_diacritics 2'
);

-- ---------------------------------------------------------------- 实体（人/群/事物）
CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    etype       TEXT NOT NULL,                      -- person|group|topic|thing|self
    name        TEXT NOT NULL,                      -- 主称呼
    aliases     TEXT NOT NULL DEFAULT '[]',         -- JSON 数组：昵称/外号
    profile     TEXT NOT NULL DEFAULT '{}',         -- JSON：年龄、职业、喜好、雷点
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    UNIQUE(etype, name)
);

-- ---------------------------------------------------------------- 关系（有向）
CREATE TABLE IF NOT EXISTS relations (
    src_id      INTEGER NOT NULL REFERENCES entities(id),
    dst_id      INTEGER NOT NULL REFERENCES entities(id),
    affinity    REAL NOT NULL DEFAULT 0.0,          -- -100..100 好感
    trust       REAL NOT NULL DEFAULT 0.5,          -- 0..1 信任
    familiarity REAL NOT NULL DEFAULT 0.0,          -- 0..1 熟悉度，只增不减
    label       TEXT,                               -- 损友/熟人/陌生人/家人
    notes       TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id)
);

-- ---------------------------------------------------------------- 语义记忆
CREATE TABLE IF NOT EXISTS facts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id   INTEGER REFERENCES entities(id),
    predicate    TEXT NOT NULL,                     -- 怕辣 / 在考研 / 讨厌被叫外号
    object_text  TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 0.6,
    evidence     TEXT NOT NULL DEFAULT '[]',        -- JSON 数组：event id
    created_at   TEXT NOT NULL,
    confirmed_at TEXT,
    disputed     INTEGER NOT NULL DEFAULT 0,
    superseded_by INTEGER                      -- 被哪条更新的事实取代（只标记，不删）
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject_id);

-- ---------------------------------------------------------------- 表达习惯（长出来的口癖）
CREATE TABLE IF NOT EXISTS expressions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    situation   TEXT NOT NULL,                      -- "对某件事表示惊叹"
    style       TEXT NOT NULL,                      -- "使用 我嘞个xxxx"
    weight      REAL NOT NULL DEFAULT 1.0,          -- 用得越多越高，用腻了会降
    embedding   BLOB,
    source      TEXT,                               -- 学自哪个群
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------- 自我状态（单行 KV，随状态更新）
CREATE TABLE IF NOT EXISTS self_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ---------------------------------------------------------------- 反思 / 日记
CREATE TABLE IF NOT EXISTS reflections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    period       TEXT NOT NULL,                     -- window|day|week
    text         TEXT NOT NULL,                     -- 第一人称
    derived_from TEXT NOT NULL DEFAULT '[]',        -- JSON：event id
    mood         TEXT
);
CREATE INDEX IF NOT EXISTS idx_reflections_period ON reflections(period, ts DESC);

-- ---------------------------------------------------------------- 关心的事（主动性的来源）
CREATE TABLE IF NOT EXISTS intents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,                       -- "想问问阿明考研怎么样了"
    target_id  INTEGER REFERENCES entities(id),
    priority   REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    due_at     TEXT,
    done_at    TEXT
);

-- ---------------------------------------------------------------- 想说话的冲动
-- 不是定时器：心里攒着一件事，强度随时间衰减，攒够了才憋不住。
CREATE TABLE IF NOT EXISTS urges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,          -- video | followup | person | boredom | thought
    about      TEXT,                   -- 关于谁 / 什么话题
    note       TEXT,                   -- 想说的由头
    strength   REAL NOT NULL DEFAULT 0.5,
    scene      TEXT,                   -- 想对谁说，空表示"当前会话"
    created_at TEXT NOT NULL,
    expires_at TEXT,
    used_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_urges_open ON urges(used_at, strength DESC);

-- ---------------------------------------------------------------- 念头（v2 主体）
-- 不是"要不要回这条消息"，是"我此刻在想什么"。说话只是念头的一种出口。
CREATE TABLE IF NOT EXISTS thoughts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,                      -- reply|pursue|wander|reflect
    content    TEXT NOT NULL,                      -- 一句话：我想……
    target     TEXT,                               -- 想对谁/在哪个场景
    urge       REAL NOT NULL DEFAULT 0.5,          -- 0..1 强度
    status     TEXT NOT NULL DEFAULT 'pending',    -- pending|acting|done|dropped
    origin     TEXT,                               -- 触发来源：event id / loop id / tick
    scene      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_thoughts_open ON thoughts(status, urge DESC);
CREATE INDEX IF NOT EXISTS idx_thoughts_ts   ON thoughts(ts DESC);

-- ---------------------------------------------------------------- 悬着的事（open loops）
-- 比 urges 更持久：不会被"用掉"，只会因为办成了或淡了而关闭。
CREATE TABLE IF NOT EXISTS open_loops (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,                      -- "那个格斗游戏想知道叫什么"
    target     TEXT,                               -- 对谁/哪个场景
    strength   REAL NOT NULL DEFAULT 0.5,
    source     TEXT,                               -- 来自哪条记忆/念头
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_loops_open ON open_loops(closed_at, strength DESC);

-- ---------------------------------------------------------------- 搜到的事（暂存区）
-- 她自己上网查到的，最多留 4 条。记不记进长期记忆由她自己定；
-- 不用当场定 —— 可以先去问人，隔几拍再回来决定。
CREATE TABLE IF NOT EXISTS search_pool (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    query      TEXT NOT NULL,                      -- 她搜的关键词
    title      TEXT NOT NULL,                      -- 搜到的那条的标题
    digest     TEXT,                               -- 一句摘要
    url        TEXT,
    state      TEXT NOT NULL DEFAULT 'open',       -- open / kept / dropped
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pool_open ON search_pool(state, id DESC);

-- ---------------------------------------------------------------- 初始状态
INSERT OR IGNORE INTO self_state(key, value, updated_at) VALUES
    ('mood',        '平静',        datetime('now','localtime')),
    ('energy',      '0.8',         datetime('now','localtime')),
    ('curiosity',   '0.7',         datetime('now','localtime')),
    ('focus',       '',            datetime('now','localtime')),
    ('age_days',    '1',           datetime('now','localtime')),
    ('born_at',     datetime('now','localtime'), datetime('now','localtime'));
