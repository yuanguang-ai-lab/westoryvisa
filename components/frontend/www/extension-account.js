/* Website login is the account link. Task tokens never enter persistent storage. */
(() => {
  'use strict';
  let extensionId='', job=null, busy=false, message='正在检测插件…', poll=null;
  const pending=new Map();
  const closed=new Set(['completed','revoked','expired','failed']);
  const t=(value)=>window.WestoryLanguage?.translate(value)||value;
  function post(type,payload={}) {
    const requestId=crypto.randomUUID();
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{pending.delete(requestId);reject(new Error('插件未响应，请重新加载插件并刷新本页'));},15000);
      pending.set(requestId,{resolve,reject,timer});
      window.postMessage({source:'docflow-app',type,requestId,...payload},location.origin);
    });
  }
  window.addEventListener('message',event=>{
    if(event.source!==window||event.origin!==location.origin||event.data?.source!=='docflow-extension')return;
    const m=event.data;
    if(m.extensionVersion==='1.0.5' && /^[a-p]{32}$/.test(m.extensionId||''))extensionId=m.extensionId;
    const p=pending.get(m.requestId);
    if(p){clearTimeout(p.timer);pending.delete(m.requestId);m.type==='DOCFLOW_TASK_ERROR'?p.reject(new Error(m.message)):p.resolve(m);}
    if(m.type==='DOCFLOW_EXTENSION_READY' && !job && m.jobId && m.active) {
      job={jobId:m.jobId,state:m.taskState,totalFields:m.totalFields,completedFields:m.completedFields}; startPolling();
    }
    if(job && m.jobId===job.jobId && m.type==='DOCFLOW_TASK_STATUS') {
      message=m.message||message;
    }
    render();
  });
  async function api(path,method='GET',body) {
    const response=await DocFlowApi.request(`/api/extension/${path}`,{
      method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||'服务器请求失败');
    return data;
  }
  function startPolling(){clearInterval(poll);poll=setInterval(async()=>{
    if(!job)return;
    try{job=await api(`jobs/${job.jobId}`);message=job.message;if(closed.has(job.state))clearInterval(poll);render();}
    catch(e){message=e.message;render();}
  },4000);}
  async function create(){
    if(busy)return;busy=true;render();
    let created;
    try{
      const application=getActiveApplication();if(!application)throw new Error('请先选择客户档案');
      if(!extensionId)throw new Error('请安装 1.0.5 正式网站版插件并刷新页面');
      if(application.codexAgent?.jobId && !closed.has(application.codexAgent.state) && !application.codexAgent.closed)
        throw new Error('请先停止此档案的服务器填写任务，再使用插件');
      created=await api('jobs','POST',{caseId:application.id,extensionId});
      await post('DOCFLOW_START_TASK',{jobId:created.jobId,taskUrl:created.taskUrl,accessToken:created.accessToken});
      job={jobId:created.jobId,state:'prepared',totalFields:created.totalFields,completedFields:0};
      message='账号已关联。进入 CEAC 正式表格后点击“开始填写”。';startPolling();
    }catch(e){message=e.message;if(created)await api(`jobs/${created.jobId}`,'DELETE').catch(()=>{});}
    finally{created=null;busy=false;render();}
  }
  async function resume(){busy=true;render();try{await post('DOCFLOW_RESUME_TASK',{jobId:job.jobId});message='插件已开始填写，请保持 CEAC 标签页打开';}catch(e){message=e.message;}finally{busy=false;render();}}
  async function stop(){busy=true;render();try{
    job=await api(`jobs/${job.jobId}`,'DELETE');
    await post('DOCFLOW_STOP_TASK',{jobId:job.jobId}).catch(()=>{});
    message='任务已停止，授权已撤销';clearInterval(poll);
  }catch(e){message=e.message;}finally{busy=false;render();}}
  function render(){
    const area=document.querySelector('.codex-agent-actions');if(!area)return;
    let panel=document.getElementById('memberExtensionPanel');
    if(!panel){panel=document.createElement('section');panel.id='memberExtensionPanel';panel.className='screen-agent-safety-note';
      panel.innerHTML=`<strong>${t('会员插件填写 · 当前电脑 Chrome')}</strong><p data-extension-message></p><p data-extension-progress></p><div class="actions"><button class="btn" data-extension-create>${t('连接当前档案')}</button><button class="btn" data-extension-resume>${t('开始填写')}</button><button class="btn secondary" data-extension-stop>${t('停止插件任务')}</button><a href="/extension.html" target="_blank" rel="noopener">${t('下载与安装插件')}</a></div>`;
      area.parentElement.append(panel);panel.querySelector('[data-extension-create]').onclick=create;panel.querySelector('[data-extension-resume]').onclick=resume;panel.querySelector('[data-extension-stop]').onclick=stop;
    }
    const setText=(selector,value)=>{const e=panel.querySelector(selector);const localized=t(value);if(e.textContent!==localized)e.textContent=localized;};
    setText('[data-extension-message]',message==='正在检测插件…'?(extensionId?'插件已连接，使用当前网站账号的会员权限':'未检测到正式网站版插件，请安装后刷新此页'):message);
    setText('[data-extension-progress]',job?`${job.pageLabel||''} ${job.completedFields||0} / ${job.totalFields||0}`:'每个账号同时一份；机构默认同时三份。无需安装本地服务。');
    const active=job&&!closed.has(job.state);
    panel.querySelector('[data-extension-create]').disabled=busy||!!active||!extensionId;
    panel.querySelector('[data-extension-resume]').hidden=!active;
    panel.querySelector('[data-extension-resume]').disabled=busy||job?.state==='running';
    panel.querySelector('[data-extension-stop]').hidden=!active;
    panel.querySelector('[data-extension-stop]').disabled=busy;
  }
  new MutationObserver(()=>render()).observe(document.getElementById('app'),{childList:true,subtree:true});
  render();post('DOCFLOW_EXTENSION_PING').catch(()=>render());
})();
