-- Runs only on first initialisation of the pgdata volume.
-- Separate database for the pytest suite (tests TRUNCATE every table, never point them at `chat`).
CREATE DATABASE chat_test;
