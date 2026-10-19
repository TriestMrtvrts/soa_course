-- ADMIN cannot self-register via /auth/register, so one admin account is seeded.
-- Login: admin@marketplace.dev / Admin12345 (bcrypt hash below)
INSERT INTO users (email, password_hash, role)
VALUES ('admin@marketplace.dev', '$2b$10$ENcia/hbaz2tp38YDt6TUO4FTzF0pyI7IhkwepE.JdSC78s7qEBbO', 'ADMIN');
