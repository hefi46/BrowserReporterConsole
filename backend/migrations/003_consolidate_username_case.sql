-- Migration 003: consolidate usernames that differ only by case
--
-- For each group of users whose usernames are identical when uppercased,
-- keep the row whose username is already uppercase (or the first row if none
-- is), reassign all visits to that canonical row, then delete the duplicates.
-- Finally, upper-case every remaining username so future inserts land on the
-- same row regardless of the case sent by the browser extension.

DO $$
DECLARE
    canonical_id   INT;
    canonical_name TEXT;
    dup            RECORD;
BEGIN
    -- Step 1: for every case-insensitive duplicate group, pick a canonical user
    -- (prefer the fully-uppercased row; otherwise take the one with the lowest id).
    FOR dup IN
        SELECT UPPER(username) AS upper_name
        FROM users
        GROUP BY UPPER(username)
        HAVING COUNT(*) > 1
    LOOP
        -- Prefer the already-uppercase row, else lowest id
        SELECT id, username
          INTO canonical_id, canonical_name
          FROM users
         WHERE UPPER(username) = dup.upper_name
         ORDER BY (username = UPPER(username)) DESC, id ASC
         LIMIT 1;

        -- Reassign visits from every other row in this group to the canonical row
        UPDATE visits
           SET user_id = canonical_id
         WHERE user_id IN (
               SELECT id FROM users
                WHERE UPPER(username) = dup.upper_name
                  AND id <> canonical_id
         );

        -- Delete the now-empty duplicate user rows
        DELETE FROM users
         WHERE UPPER(username) = dup.upper_name
           AND id <> canonical_id;

        -- Ensure the canonical row's username is uppercased
        UPDATE users SET username = UPPER(username) WHERE id = canonical_id;
    END LOOP;

    -- Step 2: uppercase all remaining usernames (non-duplicates that still have mixed case)
    UPDATE users SET username = UPPER(username) WHERE username <> UPPER(username);
END $$;
