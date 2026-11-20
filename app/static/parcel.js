const ui={message:document.querySelector('#message'),sample:document.querySelector('#sample-name'),file:document.querySelector('#scan-file')};
async function requestRun(url,body,isFile=false){ui.message.textContent='正在核算…';try{const options={method:'POST',body:isFile?body:JSON.stringify(body)};if(!isFile)options.headers={'Content-Type':'application/json'};const res=await fetch(url,options);const result=await res.json();if(!res.ok)throw new Error(result.message);window.location.reload();}catch(e){ui.message.textContent=e.message;}}
document.querySelector('#retry')?.addEventListener('click',()=>requestRun('/api/runs',{sample:ui.sample.value}));
document.querySelector('#read-file')?.addEventListener('click',()=>{if(!ui.file.files.length){ui.message.textContent='请选择扫描文件。';return;}const data=new FormData();data.append('file',ui.file.files[0]);requestRun('/api/upload',data,true);});
document.querySelector('#review-run')?.addEventListener('click',()=>requestRun('/api/runs/'+window.parcelRun.id+'/review',{version:window.parcelRun.version,decision:'已复核'}));
document.querySelector('#search-scans')?.addEventListener('input',event=>{document.querySelectorAll('#scan-rows tr').forEach(row=>{row.hidden=!row.textContent.includes(event.target.value);});});

// M6 汇率切换与补传登记：失败时后端已回滚上一可用结论，前端提示并展示回滚原因
const fxMessage=document.querySelector('#fx-message');
function flash(text,ok){if(!fxMessage)return;fxMessage.textContent=text;fxMessage.className='fx-message '+(ok?'ok':'bad');}
async function postJson(url,body){const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return{status:res.status,data:await res.json()};}
document.querySelector('#fx-switch')?.addEventListener('click',async()=>{
  const raw=document.querySelector('#fx-rates').value.trim();
  let fx_rates=null;
  if(raw){try{fx_rates=JSON.parse(raw);}catch(e){flash('汇率表不是合法 JSON，未切换',false);return;}}
  flash('正在重算…',true);
  try{
    const {status,data}=await postJson('/api/runs/'+window.parcelRun.id+'/fx',raw?{fx_rates,note:'页面汇率切换'}:{});
    if(status===200){flash('已按新汇率日重算，结论编号 '+data.run_id,true);setTimeout(()=>location.reload(),800);}
    else{flash('切换失败已回滚上一可用结论：'+data.error.message,false);}
  }catch(e){flash('网络异常，结论未改动',false);}
});
document.querySelector('#adj-submit')?.addEventListener('click',async()=>{
  const payload={parcel:document.querySelector('#adj-parcel').value.trim(),
    scan_id:document.querySelector('#adj-scan').value.trim(),
    batch_date:document.querySelector('#adj-batch-date').value,
    amount:document.querySelector('#adj-amount').value.trim(),
    currency:document.querySelector('#adj-currency').value.trim()||'CNY',
    source:'页面补传'};
  if(!payload.parcel||!payload.scan_id||!payload.batch_date||!payload.amount){flash('补传字段不完整',false);return;}
  flash('正在登记补传并重算…',true);
  try{
    const {status,data}=await postJson('/api/runs/'+window.parcelRun.id+'/adjustments',payload);
    if(status===200){flash((data.adjustment.deduplicated?'重试已识别，未重复冲销':'补传已登记')+'，结论编号 '+data.run_id,true);setTimeout(()=>location.reload(),800);}
    else{flash('重算失败已回滚：'+data.error.message,false);}
  }catch(e){flash('网络异常，结论未改动',false);}
});
