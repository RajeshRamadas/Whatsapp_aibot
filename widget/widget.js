(()=>{
  const script=document.currentScript;
  const key=script?.dataset.chatbotKey;
  const api=new URL(script.src).origin;
  if(!key)return;
  const button=document.createElement('button');button.textContent='Chat';
  button.style.cssText='position:fixed;bottom:20px;right:20px;padding:12px 20px;z-index:2147483647;cursor:pointer';
  const panel=document.createElement('section');
  panel.style.cssText='display:none;position:fixed;bottom:75px;right:20px;width:min(350px,calc(100vw - 40px));height:430px;background:white;color:#222;border:1px solid #ddd;box-shadow:0 4px 24px #0002;z-index:2147483647;font:14px sans-serif';
  const log=document.createElement('div');log.style.cssText='height:365px;overflow:auto;padding:12px;white-space:pre-wrap';
  const form=document.createElement('form');form.style.cssText='display:flex;border-top:1px solid #ddd';
  const input=document.createElement('input');input.placeholder='Ask a question';input.required=true;input.maxLength=4000;input.style.cssText='flex:1;min-width:0;padding:12px;border:0';
  const send=document.createElement('button');send.textContent='Send';send.type='submit';
  form.append(input,send);panel.append(log,form);document.body.append(button,panel);
  let conversationId=null;
  button.addEventListener('click',()=>panel.style.display=panel.style.display==='none'?'block':'none');
  const line=(label,value)=>{const p=document.createElement('p');p.textContent=label+': '+value;log.append(p);log.scrollTop=log.scrollHeight};
  form.addEventListener('submit',async e=>{
    e.preventDefault();const message=input.value.trim();if(!message)return;
    input.value='';line('You',message);send.disabled=true;
    try{
      const r=await fetch(api+'/api/v1/chat/'+encodeURIComponent(key),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,conversation_id:conversationId})});
      if(!r.ok)throw Error('Request failed ('+r.status+')');
      const data=await r.json();conversationId=data.conversation_id;line('Assistant',data.answer);
    }catch(err){line('System',err.message)}finally{send.disabled=false}
  });
})();
