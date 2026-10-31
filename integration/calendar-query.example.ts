/** Optional EXISTING SITE integration example, not a new frontend.
 * Use the app's already-authenticated Supabase client, and keep existing RLS.
 * Adapt the types to src/types/crowdstage.ts (not supplied with this task).
 */
import type { SupabaseClient } from 'npm:@supabase/supabase-js@2';
export async function listPublishedConferences(
  supabase: SupabaseClient,
  from: string,
  to: string,
  maxRows = 200,
) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to) || from > to) {
    throw new Error('Valid date range required');
  }
  // Schema: CrowdStage events, NOT former starter's published_conferences view.
  const { data, error } = await supabase.from('events')
    .select('id,slug,name,summary,kind,format,size,region,start_date,end_date,city,country,venue,attendees,external_url,cover_url,featured')
    .eq('status','published')
    .lte('start_date',to).gte('end_date',from)
    .order('start_date',{ascending:true})
    .limit(Math.min(Math.max(maxRows,1),500));
  if (error) throw error;
  return data ?? [];
}
