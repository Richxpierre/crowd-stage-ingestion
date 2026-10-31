-- Disabled template: replace URL, permission basis and dates after reviewing source rights.
-- Requires CrowdStage Phase 2 schema and 202610300100 migration.
insert into public.conference_sources(
 slug,name,publisher,kind,url,permission_basis,permission_reviewed_at,
 ingestion_allowed,is_official_source,is_active,refresh_hours
) values (
 'example-organizer-2027', 'Example Conference Organizer', 'example.org',
 'official_html', 'https://example.org/conferences/2027',
 '', null, false, false, false, 24
) on conflict(slug) do nothing;
