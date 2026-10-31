-- ETL 저장소 db/migrations 001~004, 007 복사본 (youthlink-data-pipeline 3159385)
-- region 테이블과 지역 해석 View. db/schema.sql에 포함되지 않아 테스트 DB 구성용으로 따로 둠
-- 014(대전 상위 코드 매핑)는 지역 행을 참조하는 데이터라 넣지 않음 (테스트는 지역을 직접 넣음). 7c536f7 기준 001~004·007은 바뀌지 않음

-- ===== 001_add_region_master.sql =====
BEGIN;

CREATE TABLE IF NOT EXISTS region (
    region_code VARCHAR(5) PRIMARY KEY,

    sido_code VARCHAR(2) NOT NULL,
    sigungu_code VARCHAR(3) NOT NULL,

    sido_name VARCHAR(50) NOT NULL,
    sigungu_name VARCHAR(50) NOT NULL,
    region_name VARCHAR(100) NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    source VARCHAR(50) NOT NULL DEFAULT 'VWORLD',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_region_code_length
        CHECK (LENGTH(region_code) = 5),

    CONSTRAINT chk_region_sido_code_length
        CHECK (LENGTH(sido_code) = 2),

    CONSTRAINT chk_region_sigungu_code_length
        CHECK (LENGTH(sigungu_code) = 3)
);

CREATE INDEX IF NOT EXISTS idx_region_sido_code
    ON region(sido_code);

CREATE INDEX IF NOT EXISTS idx_region_sido_name
    ON region(sido_name);

CREATE INDEX IF NOT EXISTS idx_region_sigungu_name
    ON region(sigungu_name);


CREATE TABLE IF NOT EXISTS region_code_alias (
    alias_code VARCHAR(5) PRIMARY KEY,

    canonical_code VARCHAR(5) NOT NULL
        REFERENCES region(region_code),

    valid_from DATE,
    valid_to DATE,

    reason TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_region_alias_code_length
        CHECK (LENGTH(alias_code) = 5),

    CONSTRAINT chk_region_alias_not_self
        CHECK (alias_code <> canonical_code)
);

CREATE INDEX IF NOT EXISTS idx_region_code_alias_canonical
    ON region_code_alias(canonical_code);

COMMIT;
-- ===== 002_change_region_code_mapping.sql =====
BEGIN;

CREATE TABLE IF NOT EXISTS public.region_code_mapping (
    source_code VARCHAR(5) NOT NULL,

    canonical_code VARCHAR(5) NOT NULL
        REFERENCES public.region(region_code),

    mapping_type VARCHAR(20) NOT NULL DEFAULT 'REPLACED',

    valid_from DATE,
    valid_to DATE,

    reason TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT region_code_mapping_pkey
        PRIMARY KEY (source_code, canonical_code),

    CONSTRAINT chk_region_code_mapping_source_length
        CHECK (LENGTH(source_code) = 5),

    CONSTRAINT chk_region_code_mapping_not_self
        CHECK (source_code <> canonical_code),

    CONSTRAINT chk_region_code_mapping_type
        CHECK (
            mapping_type IN (
                'REPLACED',
                'SPLIT',
                'MERGED',
                'PARENT'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_region_code_mapping_canonical
    ON public.region_code_mapping(canonical_code);

COMMIT;

--REPLACED
--옛 코드가 새로운 코드 하나로 대체

--SPLIT
--옛 지역 하나가 여러 지역으로 분리

--MERGED
--여러 옛 지역이 하나로 통합

--PARENT
--온통청년 코드가 더 넓은 상위 지역을 의미
--예: 천안시 전체 → 동남구 + 서북구
-- ===== 003_add_policy_region_resolved_view.sql =====
BEGIN;

CREATE OR REPLACE VIEW v_policy_region_resolved AS

-- =========================================================
-- 1. 과거 코드 / 상위 코드를 현재 코드로 매핑
--    1:N 매핑인 경우 여러 행으로 확장됨
-- =========================================================
SELECT
    pr.policy_no,
    pr.region_code AS source_region_code,

    m.canonical_code AS resolved_region_code,

    r.sido_code,
    r.sigungu_code,
    r.sido_name,
    r.sigungu_name,
    r.region_name,
    r.is_active,

    'MAPPED'::VARCHAR(10) AS resolution_type,

    m.mapping_type,
    m.valid_from,
    m.valid_to

FROM policy_region pr

JOIN region_code_mapping m
    ON m.source_code = pr.region_code

JOIN region r
    ON r.region_code = m.canonical_code


UNION ALL


-- =========================================================
-- 2. 이미 현재 region 코드인 경우
-- =========================================================
SELECT
    pr.policy_no,
    pr.region_code AS source_region_code,

    pr.region_code AS resolved_region_code,

    r.sido_code,
    r.sigungu_code,
    r.sido_name,
    r.sigungu_name,
    r.region_name,
    r.is_active,

    'DIRECT'::VARCHAR(10) AS resolution_type,

    NULL::VARCHAR(20) AS mapping_type,
    NULL::DATE AS valid_from,
    NULL::DATE AS valid_to

FROM policy_region pr

JOIN region r
    ON r.region_code = pr.region_code

WHERE NOT EXISTS (
    SELECT 1
    FROM region_code_mapping m
    WHERE m.source_code = pr.region_code
)


UNION ALL


-- =========================================================
-- 3. region에도 없고 mapping에도 없는 미해결 코드
--    향후 API에 신규/이상 코드가 들어왔을 때 탐지용
-- =========================================================
SELECT
    pr.policy_no,
    pr.region_code AS source_region_code,

    NULL::VARCHAR(5) AS resolved_region_code,

    NULL::VARCHAR(2) AS sido_code,
    NULL::VARCHAR(3) AS sigungu_code,
    NULL::VARCHAR(50) AS sido_name,
    NULL::VARCHAR(50) AS sigungu_name,
    NULL::VARCHAR(100) AS region_name,
    NULL::BOOLEAN AS is_active,

    'UNRESOLVED'::VARCHAR(10) AS resolution_type,

    NULL::VARCHAR(20) AS mapping_type,
    NULL::DATE AS valid_from,
    NULL::DATE AS valid_to

FROM policy_region pr

WHERE NOT EXISTS (
    SELECT 1
    FROM region r
    WHERE r.region_code = pr.region_code
)
AND NOT EXISTS (
    SELECT 1
    FROM region_code_mapping m
    WHERE m.source_code = pr.region_code
);

COMMIT;
-- ===== 004_retire_region_code_alias.sql =====
BEGIN;

DO $migration$
DECLARE
    conflicting_alias RECORD;
BEGIN
    -- This migration is safe to re-run after region_code_alias has been removed.
    IF to_regclass('public.region_code_alias') IS NULL THEN
        RETURN;
    END IF;

    -- 002_change_region_code_mapping.sql must be applied first. Raising here keeps
    -- the legacy table and its data intact when migrations are run out of order.
    IF to_regclass('public.region_code_mapping') IS NULL THEN
        RAISE EXCEPTION
            'public.region_code_mapping does not exist; apply migration 002 before migration 004';
    END IF;

    -- Prevent rows from being added between the copy and the table removal.
    LOCK TABLE public.region_code_alias IN ACCESS EXCLUSIVE MODE;

    -- The legacy schema normally prevents self mappings. Check explicitly so a
    -- database whose old constraint was removed cannot silently lose such rows.
    SELECT
        alias_code,
        canonical_code
    INTO conflicting_alias
    FROM public.region_code_alias
    WHERE alias_code = canonical_code
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION
            'cannot migrate self mapping %.% from public.region_code_alias',
            conflicting_alias.alias_code,
            conflicting_alias.canonical_code;
    END IF;

    -- A pre-existing mapping may safely contribute metadata that the alias row
    -- does not have. If both rows contain different non-null values, stop instead
    -- of choosing one value and dropping the other with the legacy table.
    SELECT
        alias_row.alias_code,
        alias_row.canonical_code
    INTO conflicting_alias
    FROM public.region_code_alias AS alias_row
    JOIN public.region_code_mapping AS mapping_row
      ON mapping_row.source_code = alias_row.alias_code
     AND mapping_row.canonical_code = alias_row.canonical_code
    WHERE (
            alias_row.valid_from IS NOT NULL
        AND mapping_row.valid_from IS NOT NULL
        AND alias_row.valid_from IS DISTINCT FROM mapping_row.valid_from
    )
       OR (
            alias_row.valid_to IS NOT NULL
        AND mapping_row.valid_to IS NOT NULL
        AND alias_row.valid_to IS DISTINCT FROM mapping_row.valid_to
    )
       OR (
            alias_row.reason IS NOT NULL
        AND mapping_row.reason IS NOT NULL
        AND alias_row.reason IS DISTINCT FROM mapping_row.reason
    )
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION
            'conflicting metadata for region mapping %.%; public.region_code_alias was not removed',
            conflicting_alias.alias_code,
            conflicting_alias.canonical_code;
    END IF;

    INSERT INTO public.region_code_mapping (
        source_code,
        canonical_code,
        mapping_type,
        valid_from,
        valid_to,
        reason,
        created_at
    )
    SELECT
        alias_code,
        canonical_code,
        'REPLACED',
        valid_from,
        valid_to,
        reason,
        created_at
    FROM public.region_code_alias
    ON CONFLICT (source_code, canonical_code) DO UPDATE
    SET
        -- Keep an existing, more specific mapping_type (for example SPLIT or
        -- PARENT), fill missing legacy metadata, and retain the earliest record
        -- creation time for the same source/canonical relationship.
        valid_from = COALESCE(
            public.region_code_mapping.valid_from,
            EXCLUDED.valid_from
        ),
        valid_to = COALESCE(
            public.region_code_mapping.valid_to,
            EXCLUDED.valid_to
        ),
        reason = COALESCE(
            public.region_code_mapping.reason,
            EXCLUDED.reason
        ),
        created_at = LEAST(
            public.region_code_mapping.created_at,
            EXCLUDED.created_at
        );
END;
$migration$;

DROP TABLE IF EXISTS public.region_code_alias;

COMMIT;

-- ===== 007_complete_incheon_scope_and_add_scope_views.sql =====
BEGIN;

-- Official references for the 2026-07-01 Incheon reorganization:
-- - Ministry of the Interior and Safety code release:
--   https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?bbsId=BBSMSTR_000000000052&nttId=127039
-- - Incheon Metropolitan City overview:
--   https://www.incheon.go.kr/IC01070101
--
-- Some Youth Policy API records already replaced the old Jung-gu/Seo-gu codes
-- with only one successor each. When, and only when, a policy has exactly the
-- nine Incheon codes below, complete that city-wide set with Yeongjong-gu and
-- Seohae-gu. policy_region remains unchanged as the raw source record.
CREATE OR REPLACE VIEW public.v_policy_region_resolved AS
WITH incheon_scope_candidates AS (
    SELECT pr.policy_no
    FROM public.policy_region AS pr
    WHERE pr.region_code LIKE '28%'
    GROUP BY pr.policy_no
    HAVING ARRAY_AGG(DISTINCT pr.region_code ORDER BY pr.region_code)
        = ARRAY[
            '28125', '28177', '28185', '28200', '28237',
            '28245', '28290', '28710', '28720'
        ]::VARCHAR[]
),
incheon_scope_completion AS (
    SELECT
        candidate.policy_no,
        inferred.resolved_region_code
    FROM incheon_scope_candidates AS candidate
    CROSS JOIN (
        VALUES ('28155'::VARCHAR(5)), ('28275'::VARCHAR(5))
    ) AS inferred(resolved_region_code)
)

-- 1. Historical or parent code expanded through the mapping table.
SELECT
    pr.policy_no,
    pr.region_code AS source_region_code,
    m.canonical_code AS resolved_region_code,
    r.sido_code,
    r.sigungu_code,
    r.sido_name,
    r.sigungu_name,
    r.region_name,
    r.is_active,
    'MAPPED'::VARCHAR(10) AS resolution_type,
    m.mapping_type,
    m.valid_from,
    m.valid_to
FROM public.policy_region AS pr
JOIN public.region_code_mapping AS m
  ON m.source_code = pr.region_code
JOIN public.region AS r
  ON r.region_code = m.canonical_code

UNION ALL

-- 2. Source code already matches the current region master.
SELECT
    pr.policy_no,
    pr.region_code AS source_region_code,
    pr.region_code AS resolved_region_code,
    r.sido_code,
    r.sigungu_code,
    r.sido_name,
    r.sigungu_name,
    r.region_name,
    r.is_active,
    'DIRECT'::VARCHAR(10) AS resolution_type,
    NULL::VARCHAR(20) AS mapping_type,
    NULL::DATE AS valid_from,
    NULL::DATE AS valid_to
FROM public.policy_region AS pr
JOIN public.region AS r
  ON r.region_code = pr.region_code
WHERE NOT EXISTS (
    SELECT 1
    FROM public.region_code_mapping AS m
    WHERE m.source_code = pr.region_code
)

UNION ALL

-- 3. Complete the known nine-code Incheon scope anomaly. There is no single
-- original code for an inferred row, so source_region_code is intentionally
-- NULL rather than pretending that a different source code produced it.
SELECT
    completion.policy_no,
    NULL::VARCHAR(20) AS source_region_code,
    completion.resolved_region_code,
    r.sido_code,
    r.sigungu_code,
    r.sido_name,
    r.sigungu_name,
    r.region_name,
    r.is_active,
    'INFERRED'::VARCHAR(10) AS resolution_type,
    'SCOPE_COMPLETION'::VARCHAR(20) AS mapping_type,
    DATE '2026-07-01' AS valid_from,
    NULL::DATE AS valid_to
FROM incheon_scope_completion AS completion
JOIN public.region AS r
  ON r.region_code = completion.resolved_region_code

UNION ALL

-- 4. Code cannot be resolved by either the current master or a mapping.
SELECT
    pr.policy_no,
    pr.region_code AS source_region_code,
    NULL::VARCHAR(5) AS resolved_region_code,
    NULL::VARCHAR(2) AS sido_code,
    NULL::VARCHAR(3) AS sigungu_code,
    NULL::VARCHAR(50) AS sido_name,
    NULL::VARCHAR(50) AS sigungu_name,
    NULL::VARCHAR(100) AS region_name,
    NULL::BOOLEAN AS is_active,
    'UNRESOLVED'::VARCHAR(10) AS resolution_type,
    NULL::VARCHAR(20) AS mapping_type,
    NULL::DATE AS valid_from,
    NULL::DATE AS valid_to
FROM public.policy_region AS pr
WHERE NOT EXISTS (
    SELECT 1
    FROM public.region AS r
    WHERE r.region_code = pr.region_code
)
AND NOT EXISTS (
    SELECT 1
    FROM public.region_code_mapping AS m
    WHERE m.source_code = pr.region_code
);


-- One row per policy. This is the shared nationwide/partial/unknown contract
-- for the backend and RAG instead of duplicating set-comparison logic.
CREATE OR REPLACE VIEW public.v_policy_region_scope AS
WITH active_region_total AS (
    SELECT COUNT(*)::INTEGER AS region_count
    FROM public.region
    WHERE is_active = TRUE
),
source_stats AS (
    SELECT policy_no, COUNT(*)::INTEGER AS source_region_count
    FROM public.policy_region
    GROUP BY policy_no
),
resolved_stats AS (
    SELECT
        policy_no,
        COUNT(DISTINCT resolved_region_code) FILTER (
            WHERE is_active = TRUE
        )::INTEGER AS resolved_active_region_count,
        COUNT(*) FILTER (
            WHERE resolution_type = 'UNRESOLVED'
        )::INTEGER AS unresolved_region_count,
        COUNT(*) FILTER (
            WHERE is_active = FALSE
        )::INTEGER AS inactive_region_count
    FROM public.v_policy_region_resolved
    GROUP BY policy_no
)
SELECT
    p.policy_no,
    CASE
        WHEN total.region_count = 0 THEN 'UNKNOWN'
        WHEN COALESCE(source.source_region_count, 0) = 0 THEN 'UNKNOWN'
        WHEN COALESCE(resolved.unresolved_region_count, 0) > 0 THEN 'UNKNOWN'
        WHEN COALESCE(resolved.inactive_region_count, 0) > 0 THEN 'UNKNOWN'
        WHEN COALESCE(resolved.resolved_active_region_count, 0)
             = total.region_count THEN 'NATIONWIDE'
        ELSE 'PARTIAL'
    END::VARCHAR(10) AS region_scope,
    COALESCE(source.source_region_count, 0) AS source_region_count,
    COALESCE(resolved.resolved_active_region_count, 0)
        AS resolved_active_region_count,
    total.region_count AS total_active_region_count,
    COALESCE(resolved.unresolved_region_count, 0) AS unresolved_region_count,
    COALESCE(resolved.inactive_region_count, 0) AS inactive_region_count
FROM public.policy AS p
CROSS JOIN active_region_total AS total
LEFT JOIN source_stats AS source
  ON source.policy_no = p.policy_no
LEFT JOIN resolved_stats AS resolved
  ON resolved.policy_no = p.policy_no;


-- One row per policy and selected sido. A full sido set is explicitly marked
-- so UI/RAG code can render "{sido_name} 전체" consistently.
CREATE OR REPLACE VIEW public.v_policy_sido_scope AS
WITH active_sido_total AS (
    SELECT
        sido_code,
        MIN(sido_name) AS sido_name,
        COUNT(*)::INTEGER AS total_active_region_count
    FROM public.region
    WHERE is_active = TRUE
    GROUP BY sido_code
),
selected AS (
    SELECT
        policy_no,
        sido_code,
        COUNT(DISTINCT resolved_region_code)::INTEGER
            AS resolved_active_region_count
    FROM public.v_policy_region_resolved
    WHERE is_active = TRUE
      AND resolution_type <> 'UNRESOLVED'
    GROUP BY policy_no, sido_code
)
SELECT
    selected.policy_no,
    selected.sido_code,
    total.sido_name,
    CASE
        WHEN selected.resolved_active_region_count
             = total.total_active_region_count THEN 'SIDO_WIDE'
        ELSE 'PARTIAL'
    END::VARCHAR(10) AS sido_scope,
    selected.resolved_active_region_count,
    total.total_active_region_count
FROM selected
JOIN active_sido_total AS total
  ON total.sido_code = selected.sido_code;

COMMENT ON VIEW public.v_policy_region_scope IS
    'One-row-per-policy region scope shared by backend and RAG';
COMMENT ON VIEW public.v_policy_sido_scope IS
    'Per-policy, per-sido coverage shared by backend and RAG';

COMMIT;

