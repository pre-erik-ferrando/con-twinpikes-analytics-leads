-- Users of the project. Passwords are set by whoever administers the instance, never here.
CREATE USER IF NOT EXISTS <usr_leadgen> IDENTIFIED WITH sha256_password BY '<set by the administrator>';
-- Intelek's user exists already (shared with the Insight Generator); only the grants are new.
