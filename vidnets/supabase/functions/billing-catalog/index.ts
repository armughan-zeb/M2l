import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { verifyPolarPlan } from '../_shared/polar-catalog.ts';

const headers = {'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers':'authorization, apikey, content-type','Content-Type':'application/json'};
const recurring=['base_monthly','base_yearly','basic_monthly','basic_yearly','plus_monthly','plus_yearly','max_monthly','max_yearly'];
const json=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers});
Deno.serve(async request=>{
  if(request.method==='OPTIONS') return new Response('ok',{headers});
  if(!['GET','POST'].includes(request.method)) return json({error:'Method not allowed'},405);
  const admin=request.method==='POST';
  if(admin){
    const authorization=request.headers.get('Authorization')||'';
    if(!authorization.startsWith('Bearer ')) return json({error:'Admin authorization required'},403);
    const caller=createClient(Deno.env.get('SUPABASE_URL')!,Deno.env.get('SUPABASE_ANON_KEY')!,
      {global:{headers:{Authorization:authorization}},auth:{persistSession:false}});
    const {error}=await caller.rpc('get_vidnets_admin_metrics');
    if(error) return json({error:'Admin authorization required'},403);
    const body=await request.json().catch(()=>({}));
    if(body.action!=='retire_recurring') return json({error:'Only retire_recurring is supported'},400);
  }
  const client=createClient(Deno.env.get('SUPABASE_URL')!,Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,{auth:{persistSession:false}});
  const {data:plans,error}=await client.from('plans').select('*').eq('is_active',true)
    .in('plan_key',admin?recurring:['lifetime_pro']);
  if(error) return json({error:'Catalog unavailable'},503);
  const results=await Promise.all((plans||[]).map(async plan=>{
    try{
      const verified=await verifyPolarPlan(plan,admin);
      if(admin){
        const {error:updateError}=await client.from('plans').update({is_available_for_purchase:false,updated_at:new Date().toISOString()}).eq('plan_key',plan.plan_key);
        if(updateError) throw updateError;
        if(!verified.product.is_archived){
          const archived=await fetch(`${(Deno.env.get('POLAR_API_BASE_URL')||'https://api.polar.sh').replace(/\/$/,'')}/v1/products/${verified.productId}`,{
            method:'PATCH',headers:{Authorization:`Bearer ${Deno.env.get('POLAR_ACCESS_TOKEN')}`,'Content-Type':'application/json'},
            body:JSON.stringify({is_archived:true}),signal:AbortSignal.timeout(10000)});
          if(!archived.ok) throw new Error('Product could not be archived');
        }
        return {plan_key:plan.plan_key,available:false,archived:true,verified:true};
      }
      return {plan_key:plan.plan_key,name:plan.display_name,billing_period:plan.billing_period,
        amount_cents:verified.amount,monthly_equivalent:Number(plan.price_monthly_display),currency:'USD',
        video_limit_minutes:plan.video_limit_minutes,export_limit:plan.metadata?.monthly_export_limit??null,
        device_limit:2,available:plan.is_available_for_purchase===true,verified:true};
    }catch(_){return {plan_key:plan.plan_key,available:false,verified:false};}
  }));
  return json({policy:'lifetime_only',trials:{admin_only:true,public_signup:false},plans:results});
});
