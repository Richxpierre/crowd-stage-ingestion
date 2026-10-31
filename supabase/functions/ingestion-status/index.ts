// Authenticated CrowdStage platform-admin-only ingestion health endpoint.
// Uses the signed-in user's JWT for role verification, BEFORE service-role access.
import { createClient } from 'npm:@supabase/supabase-js@2';
const cors = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'authorization, apikey, content-type', 'access-control-allow-methods': 'GET, OPTIONS' };
const json = (value: unknown, status = 200) => Response.json(value, { status, headers: { ...cors, 'cache-control': 'no-store' } });
Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, {status:204,headers:cors});
  if (req.method !== 'GET') return json({error:'Method not allowed'},405);
  const jwt = (req.headers.get('authorization') || '').replace(/^Bearer\s+/i, '');
  const url = Deno.env.get('SUPABASE_URL');
  const publicKey = Deno.env.get('SUPABASE_ANON_KEY') || Deno.env.get('SUPABASE_PUBLISHABLE_KEY');
  const adminKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
  if (!url || !publicKey || !adminKey) return json({error:'Server not configured'},503);
  if (!jwt) return json({error:'Authentication required'},401);
  const userClient = createClient(url, publicKey, {global:{headers:{Authorization:`Bearer ${jwt}`}},auth:{persistSession:false}});
  const {data:auth,error:authError} = await userClient.auth.getUser(jwt);
  if (authError || !auth.user) return json({error:'Invalid session'},401);
  // Uses the existing CrowdStage RLS policy: each user may read their own user_roles.
  const {data:role,error:roleError} = await userClient.from('user_roles').select('role')
    .eq('user_id',auth.user.id).eq('role','admin').maybeSingle();
  if (roleError || !role) return json({error:'Admin role required'},403);
  const admin = createClient(url, adminKey, {auth:{persistSession:false}});
  const [sources, runs, candidates] = await Promise.all([
    admin.from('conference_sources').select('id,slug,name,kind,url,is_active,last_success_at,last_error,next_check_at').order('slug').limit(500),
    admin.from('conference_ingestion_runs').select('source_id,started_at,status,fetched_count,created_count,updated_count,skipped_count,error_message').order('started_at',{ascending:false}).limit(50),
    admin.from('conference_url_candidates').select('url',{count:'exact',head:true}).eq('review_state','needs_review'),
  ]);
  if (sources.error || runs.error || candidates.error) return json({error:'Database read failed'},502);
  return json({sources:sources.data,recent_runs:runs.data,unreviewed_candidates:candidates.count});
});
