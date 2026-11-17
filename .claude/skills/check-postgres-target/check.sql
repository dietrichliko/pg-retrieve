-- Read-only inspection of a PostgreSQL target for pg-retrieve.
\pset title 'server'
SELECT version(), current_database(), current_user;

\pset title 'extensions of interest (available on the server)'
SELECT name, default_version, installed_version, left(comment, 60) AS comment
FROM pg_available_extensions
WHERE name IN ('vector', 'pg_textsearch', 'pg_trgm', 'unaccent', 'btree_gin', 'pg_cron', 'pg_stat_statements')
ORDER BY name;

\pset title 'installed in this database'
SELECT extname, extversion FROM pg_extension ORDER BY extname;

\pset title 'text search configs'
SELECT cfgname FROM pg_ts_config ORDER BY cfgname;

\pset title 'settings'
SELECT name, setting, unit
FROM pg_settings
WHERE name IN ('shared_preload_libraries', 'shared_buffers', 'work_mem', 'maintenance_work_mem',
               'max_connections', 'max_parallel_maintenance_workers', 'default_text_search_config',
               'hnsw.ef_search', 'pg_textsearch.memory_limit')
ORDER BY name;

\pset title 'privileges'
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole,
       has_database_privilege(current_database(), 'CREATE') AS can_create_in_db
FROM pg_roles WHERE rolname = current_user;

\pset title 'all available extensions (count)'
SELECT count(*) AS available FROM pg_available_extensions;
