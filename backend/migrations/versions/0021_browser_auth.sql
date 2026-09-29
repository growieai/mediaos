-- No password, setup token hash or session hash is readable by the runtime role.
-- These are identity infrastructure, not tenant-readable business artifacts.
CREATE TABLE private.user_logins (
 principal_id uuid PRIMARY KEY REFERENCES public.principals(id),
 tenant_id uuid NOT NULL,
 email text NOT NULL UNIQUE CHECK(email=lower(btrim(email)) AND length(email) BETWEEN 3 AND 254),
 password_hash text CHECK(password_hash IS NULL OR password_hash ~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'),
 generation integer NOT NULL DEFAULT 1 CHECK(generation>0),
 created_at timestamptz NOT NULL DEFAULT now(), password_changed_at timestamptz,
 FOREIGN KEY(tenant_id,principal_id) REFERENCES public.tenant_memberships(tenant_id,principal_id),
 UNIQUE(tenant_id,principal_id)
);
CREATE TABLE private.login_setups (
 token_hash text PRIMARY KEY CHECK(token_hash ~ '^[0-9a-f]{64}$'),
 principal_id uuid NOT NULL REFERENCES private.user_logins(principal_id),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 consumed_at timestamptz, CHECK(expires_at>created_at)
);
CREATE TABLE private.browser_sessions (
 token_hash text PRIMARY KEY CHECK(token_hash ~ '^[0-9a-f]{64}$'),
 principal_id uuid NOT NULL, tenant_id uuid NOT NULL, generation integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 revoked_at timestamptz, CHECK(expires_at>created_at),
 FOREIGN KEY(tenant_id,principal_id) REFERENCES private.user_logins(tenant_id,principal_id)
);
CREATE INDEX browser_sessions_principal ON private.browser_sessions(principal_id);
CREATE TABLE private.login_throttles (
 bucket text PRIMARY KEY, attempts integer NOT NULL CHECK(attempts>0),
 window_ends_at timestamptz NOT NULL
);
CREATE INDEX login_throttles_expiry ON private.login_throttles(window_ends_at);
CREATE TABLE private.auth_audit (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now(),
 event text NOT NULL CHECK(event IN ('LOGIN','SETUP','LOGOUT','INVITE','RESET','REVOKE')),
 outcome text NOT NULL CHECK(outcome IN ('SUCCESS','DENIED','THROTTLED')),
 principal_id uuid REFERENCES public.principals(id), tenant_id uuid REFERENCES public.tenants(id)
);
REVOKE ALL ON private.user_logins,private.login_setups,private.browser_sessions,
 private.login_throttles,private.auth_audit FROM PUBLIC,mediaos_runtime;

-- Fixed windows are persisted and serialized across workers. Returning a failed
-- result rather than raising is essential: the API commits failed attempts.
CREATE FUNCTION private.consume_login_limit(bucket_key text, maximum integer, seconds integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE n integer;
 BEGIN
 -- Only globally admitted attempts create account buckets. Bound cleanup work.
 IF bucket_key IN ('GLOBAL','SETUP_GLOBAL') THEN
 DELETE FROM private.login_throttles WHERE bucket IN
 (SELECT bucket FROM private.login_throttles WHERE window_ends_at<clock_timestamp()-interval '1 day' LIMIT 100);
 END IF;
 INSERT INTO private.login_throttles(bucket,attempts,window_ends_at)
 VALUES(bucket_key,1,clock_timestamp()+make_interval(secs=>seconds))
 ON CONFLICT(bucket) DO UPDATE SET
 attempts=CASE WHEN private.login_throttles.window_ends_at<=clock_timestamp() THEN 1
   ELSE least(private.login_throttles.attempts+1,maximum+2) END,
 window_ends_at=CASE WHEN private.login_throttles.window_ends_at<=clock_timestamp()
   THEN clock_timestamp()+make_interval(secs=>seconds) ELSE private.login_throttles.window_ends_at END
 RETURNING attempts INTO n;
 IF n=maximum+1 THEN
 INSERT INTO private.auth_audit(event,outcome) VALUES(
 CASE WHEN bucket_key='SETUP_GLOBAL' THEN 'SETUP' ELSE 'LOGIN' END,'THROTTLED');
 END IF;
 RETURN n<=maximum;
 END $$;

CREATE FUNCTION private.new_browser_session(p uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE u private.user_logins; rawtoken text; ending timestamptz; rolenames text[]; tname text;
 BEGIN
 SELECT * INTO STRICT u FROM private.user_logins WHERE principal_id=p FOR UPDATE;
 SELECT m.roles,t.name INTO rolenames,tname FROM public.tenant_memberships m
 JOIN public.tenants t ON t.id=m.tenant_id JOIN public.principals x ON x.id=m.principal_id
 WHERE m.principal_id=p AND m.tenant_id=u.tenant_id AND x.active;
 IF rolenames IS NULL THEN RETURN jsonb_build_object('status','DENIED'); END IF;
 rawtoken:=encode(public.gen_random_bytes(32),'hex'); ending:=clock_timestamp()+interval '8 hours';
 INSERT INTO private.browser_sessions(token_hash,principal_id,tenant_id,generation,expires_at)
 VALUES(encode(public.digest(rawtoken,'sha256'),'hex'),p,u.tenant_id,u.generation,ending);
 RETURN jsonb_build_object('status','SUCCESS','token',rawtoken,'session',jsonb_build_object(
 'email',u.email,'tenant_id',u.tenant_id,'tenant_name',tname,'roles',rolenames,'expires_at',ending));
 END $$;

CREATE FUNCTION public.browser_login(email_input text,password_input text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE u private.user_logins; hashed text; actual text; allowed boolean; result jsonb;
 BEGIN
 IF email_input IS NULL OR password_input IS NULL OR length(email_input)>254
 OR octet_length(password_input)>72 OR length(password_input)<15 THEN
 RETURN jsonb_build_object('status','DENIED'); END IF;
 allowed:=private.consume_login_limit('GLOBAL',120,60);
 IF allowed THEN allowed:=private.consume_login_limit('EMAIL:'||encode(public.digest(lower(btrim(email_input)),'sha256'),'hex'),10,900); END IF;
 IF NOT allowed THEN
 RETURN jsonb_build_object('status','THROTTLED','retry_after',900); END IF;
 SELECT l.* INTO u FROM private.user_logins l JOIN public.principals p ON p.id=l.principal_id
 WHERE l.email=lower(btrim(email_input)) AND p.active FOR UPDATE OF l;
 -- Constant work for unknown and pending/disabled accounts; bcrypt cost is 12.
 hashed:=coalesce(u.password_hash,'$2a$12$abcdefghijklmnopqrstuuT6qCmRFBwtLJK1CQ.JUZzEU1NEa1d6C');
 actual:=public.crypt(password_input,hashed);
 IF u.principal_id IS NULL OR u.password_hash IS NULL OR actual IS DISTINCT FROM u.password_hash THEN
 INSERT INTO private.auth_audit(event,outcome) VALUES('LOGIN','DENIED');
 RETURN jsonb_build_object('status','DENIED'); END IF;
 result:=private.new_browser_session(u.principal_id);
 INSERT INTO private.auth_audit(event,outcome,principal_id,tenant_id)
 VALUES('LOGIN',result->>'status',u.principal_id,u.tenant_id);
 RETURN result;
 END $$;

CREATE FUNCTION public.browser_setup(setup_token text,password_input text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE invitation private.login_setups; u private.user_logins; result jsonb;
 BEGIN
 IF setup_token IS NULL OR setup_token !~ '^[0-9a-f]{64}$' OR password_input IS NULL
 OR octet_length(password_input)>72 OR length(password_input)<15 THEN
 RETURN jsonb_build_object('status','DENIED'); END IF;
 IF NOT private.consume_login_limit('SETUP_GLOBAL',30,60) THEN
 RETURN jsonb_build_object('status','THROTTLED','retry_after',60); END IF;
 -- Lock account first, consistently with maintenance/reset and session creation.
 SELECT l.* INTO u FROM private.user_logins l JOIN private.login_setups s ON s.principal_id=l.principal_id
 JOIN public.principals p ON p.id=l.principal_id
 WHERE s.token_hash=encode(public.digest(setup_token,'sha256'),'hex') AND p.active FOR UPDATE OF l;
 SELECT * INTO invitation FROM private.login_setups
 WHERE token_hash=encode(public.digest(setup_token,'sha256'),'hex') FOR UPDATE;
 IF u.principal_id IS NULL OR invitation.consumed_at IS NOT NULL OR invitation.expires_at<=clock_timestamp() THEN
 INSERT INTO private.auth_audit(event,outcome) VALUES('SETUP','DENIED');
 RETURN jsonb_build_object('status','DENIED'); END IF;
 UPDATE private.user_logins SET password_hash=public.crypt(password_input,public.gen_salt('bf',12)),
 generation=generation+1,password_changed_at=clock_timestamp() WHERE principal_id=u.principal_id;
 UPDATE private.login_setups SET consumed_at=clock_timestamp() WHERE principal_id=u.principal_id AND consumed_at IS NULL;
 UPDATE private.browser_sessions SET revoked_at=clock_timestamp() WHERE principal_id=u.principal_id AND revoked_at IS NULL;
 result:=private.new_browser_session(u.principal_id);
 INSERT INTO private.auth_audit(event,outcome,principal_id,tenant_id)
 VALUES('SETUP','SUCCESS',u.principal_id,u.tenant_id);
 RETURN result;
 END $$;

CREATE FUNCTION public.browser_session_info(session_token text) RETURNS jsonb
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('email',l.email,'tenant_id',s.tenant_id,'tenant_name',t.name,
 'roles',m.roles,'expires_at',s.expires_at)
 FROM private.browser_sessions s JOIN private.user_logins l ON l.principal_id=s.principal_id
 JOIN public.principals p ON p.id=s.principal_id
 JOIN public.tenant_memberships m ON m.tenant_id=s.tenant_id AND m.principal_id=s.principal_id
 JOIN public.tenants t ON t.id=s.tenant_id
 WHERE s.token_hash=encode(public.digest(session_token,'sha256'),'hex') AND p.active
 AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND s.generation=l.generation
 $$;

CREATE FUNCTION public.browser_logout(session_token text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE s private.browser_sessions;
 BEGIN
 UPDATE private.browser_sessions SET revoked_at=clock_timestamp()
 WHERE token_hash=encode(public.digest(session_token,'sha256'),'hex') AND revoked_at IS NULL RETURNING * INTO s;
 IF s.principal_id IS NOT NULL THEN INSERT INTO private.auth_audit(event,outcome,principal_id,tenant_id)
 VALUES('LOGOUT','SUCCESS',s.principal_id,s.tenant_id); END IF;
 END $$;

CREATE OR REPLACE FUNCTION public.authenticate(token text,tenant uuid) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE p uuid;
 BEGIN
 SELECT x.id INTO p FROM public.principals x JOIN public.tenant_memberships m ON m.principal_id=x.id
 WHERE x.active AND x.token_hash=encode(public.digest(token,'sha256'),'hex') AND m.tenant_id=tenant;
 IF p IS NULL THEN
 SELECT s.principal_id INTO p FROM private.browser_sessions s
 JOIN private.user_logins l ON l.principal_id=s.principal_id
 JOIN public.principals x ON x.id=s.principal_id
 JOIN public.tenant_memberships m ON m.principal_id=s.principal_id AND m.tenant_id=s.tenant_id
 WHERE s.token_hash=encode(public.digest(token,'sha256'),'hex') AND s.tenant_id=tenant AND x.active
 AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND s.generation=l.generation;
 END IF;
 IF p IS NULL THEN RAISE EXCEPTION 'Unauthenticated or tenant membership denied' USING ERRCODE='28000'; END IF;
 DELETE FROM private.request_context c WHERE NOT EXISTS(SELECT 1 FROM pg_stat_activity a WHERE a.pid=c.pid);
 INSERT INTO private.request_context VALUES(pg_backend_pid(),txid_current(),tenant,p)
 ON CONFLICT(pid) DO UPDATE SET tx=excluded.tx,tenant_id=excluded.tenant_id,principal_id=excluded.principal_id;
 RETURN p;
 END $$;

REVOKE ALL ON FUNCTION private.consume_login_limit(text,integer,integer),private.new_browser_session(uuid),
 public.browser_login(text,text),public.browser_setup(text,text),public.browser_session_info(text),public.browser_logout(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.browser_login(text,text),public.browser_setup(text,text),
 public.browser_session_info(text),public.browser_logout(text) TO mediaos_runtime;
