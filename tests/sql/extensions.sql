-- ETL 저장소 db/extensions.sql 복사본 (youthlink-data-pipeline 7680c67, PR #5)
-- 테스트 DB 구성용. ETL 원본이 바뀌면 함께 갱신함

-- YouthLink PostgreSQL extensions
-- Run this before db/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
