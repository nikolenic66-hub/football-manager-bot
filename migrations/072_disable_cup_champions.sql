-- Cup and Champions League are no longer active competitions.

-- Prevent previously queued events from starting new cup/Champions League matches.
UPDATE season_events
SET status='CANCELLED',
    last_error=COALESCE(last_error, 'Cup and Champions League are disabled'),
    processing_at=NULL,
    processed_at=COALESCE(processed_at, now())
WHERE event_type IN ('MATCH_CUP','MATCH_CHAMPIONS')
  AND status IN ('PENDING','PROCESSING');
-- Keep completed tournament results and all records for historical compatibility;
-- cancel only unfinished fixtures and terminalize their linked match records without rewards.

ALTER TABLE cup_matches DROP CONSTRAINT IF EXISTS cup_matches_status_check;
ALTER TABLE cup_matches ADD CONSTRAINT cup_matches_status_check
    CHECK (status IN ('SCHEDULED','LIVE','FINISHED','CANCELLED'));

ALTER TABLE champions_matches DROP CONSTRAINT IF EXISTS champions_matches_status_check;
ALTER TABLE champions_matches ADD CONSTRAINT champions_matches_status_check
    CHECK (status IN ('SCHEDULED','LIVE','FINISHED','CANCELLED'));

UPDATE matches m
SET status='FAILED',
    live_phase='FINISHED',
    halftime_locked=FALSE,
    finished_at=COALESCE(finished_at,now())
WHERE m.status IN ('SCHEDULED','LIVE')
  AND (
      UPPER(COALESCE(m.competition_type,'')) IN ('CUP','CHAMPIONS','CHAMPIONS_LEAGUE','UCL')
      OR EXISTS (SELECT 1 FROM cup_matches cm WHERE cm.match_id=m.id)
      OR EXISTS (SELECT 1 FROM champions_matches clm WHERE clm.match_id=m.id)
  );

UPDATE cup_matches
SET status='CANCELLED'
WHERE status IN ('SCHEDULED','LIVE');

UPDATE champions_matches
SET status='CANCELLED'
WHERE status IN ('SCHEDULED','LIVE');

UPDATE cups SET status='FINISHED' WHERE status IN ('WAITING','ACTIVE');
UPDATE champions_league SET status='FINISHED' WHERE status IN ('WAITING','ACTIVE');
