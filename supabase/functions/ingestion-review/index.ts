// Admin publishing action for imported conference rows. No organizer/event ownership changes.
// Caller supplies an authenticated platform-admin user JWT; no frontend service-role key.
import { createClient } from 'npm:@supabase/supabase-js@2';
const cors = {'access-control-allow-origin':'*','access-control-allow-headers':'authorization, apikey, content-type','access-control-allow-methods':'POST, OPTIONS'};
const json=(value:unknown,status=200)=>Response.json(value,{status,headers:{...cors,'cache-control':'no-store'}});
Deno.serve(async (req)=>{
  if(req.method==='OPTIONS') return new Response(null,{status:204,headers:cors});
  if(req.method!=='POST') return json({error:'Method not allowed'},405);
  const jwt=(req.headers.get('authorization')||'').replace(/^Bearer\s+/i,'');
  const url=Deno.env.get('SUPABASE_URL');
  const anon=Deno.env.get('SUPABASE_ANON_KEY')||Deno.env.get('SUPABASE_PUBLISHABLE_KEY');
  const secret=Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
  if(!url||!anon||!secret) return json({error:'Server not configured'},503);
  if(!jwt) return json({error:'Authentication required'},401);
  const userClient=createClient(url,anon,{global:{headers:{Authorization:`Bearer ${jwt}`}},auth:{persistSession:false}});
  const {data:auth,error:authError}=await userClient.auth.getUser(jwt);
  if(authError||!auth.user) return json({error:'Invalid session'},401);
  const {data:role,error:roleError}=await userClient.from('user_roles').select('role')
    .eq('user_id',auth.user.id).eq('role','admin').maybeSingle();
  if(roleError||!role) return json({error:'Platform admin required'},403);
  let input:unknown;
  try { input=await req.json(); } catch {return json({error:'Invalid JSON'},400);}
  if(!input||typeof input!=='object') return json({error:'Invalid body'},400);
  const {event_id,action} = input as Record<string,unknown>;
  if(typeof event_id!=='string'|| !/^[0-9a-fA-F-]{36}$/.test(event_id)||!['publish','reject'].includes(String(action)))
    return json({error:'Provide event_id and action publish|reject'},400);
  const admin=createClient(url,secret,{auth:{persistSession:false}});
  const {data:evt,error:lookupErr}=await admin.from('events').select('id,org_id,source,status')
    .eq('id',event_id).maybeSingle();
  if(lookupErr||!evt) return json({error:'Event not found'},404);
  if(evt.org_id!==null||evt.source!=='ingested') return json({error:'Only unclaimed ingested events can be reviewed here'},409);
  if(evt.status==='archived') return json({error:'Archived/cancelled event cannot be published'},409);
  const newStatus=action==='publish'?'published':'rejected';
  const changes:Record<string,unknown>={status:newStatus,updated_at:new Date().toISOString()};
  if(newStatus==='published') changes.published_at=new Date().toISOString();
  // Write as the authenticated admin so CrowdStage is_admin(auth.uid()) triggers still work.
  // Conditional update guards against an organizer claim between read and write.
  const {data:updated,error:updateErr}=await userClient.from('events').update(changes).eq('id',event_id)
    .eq('source','ingested').is('org_id',null).neq('status','archived')
    .select('id,status').maybeSingle();
  if(updateErr) return json({error:'Event update failed'},502);
  if(!updated) return json({error:'Event ownership/status changed during review'},409);
  const {error:auditErr}=await admin.from('audit_log').insert({
    actor_id:auth.user.id,action:`conference.ingestion.${action}`,entity:'events',entity_id:event_id,
    diff:{status_before:evt.status,status_after:newStatus},
  });
  if(auditErr) {
    // IMPORTANT: update and audit are separate HTTP calls; report this as partial success.
    return json({event:updated,warning:'Event updated, but audit insert failed: investigate immediately'},500);
  }
  return json({event:updated});
});
