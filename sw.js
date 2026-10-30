// 法律 Podcast 離線：網路優先，斷網時從 IndexedDB 拿殼（同網域其他 app 更新會清 Cache Storage，所以不用 Cache Storage）
// 音檔不經這裡：頁面把下載好的音檔存 IndexedDB，直接用 blob 播。
var V='lp-v1',SHELL=['./','index.html','episodes.json','manifest.webmanifest','icon-192.png','icon-512.png','apple-touch-icon.png'];
function db(){return new Promise(function(ok,no){var r=indexedDB.open('law-podcast-shell',1);r.onupgradeneeded=function(){r.result.createObjectStore('f')};r.onsuccess=function(){ok(r.result)};r.onerror=function(){no(r.error)}})}
function put(k,v){return db().then(function(d){return new Promise(function(ok){var t=d.transaction('f','readwrite');t.objectStore('f').put(v,k);t.oncomplete=ok;t.onerror=ok})})}
function get(k){return db().then(function(d){return new Promise(function(ok){var r=d.transaction('f').objectStore('f').get(k);r.onsuccess=function(){ok(r.result)};r.onerror=function(){ok(null)}})})}
function key(u){var p=new URL(u,self.registration.scope).pathname,b=new URL(self.registration.scope).pathname;p=p.slice(b.length);return p===''?'index.html':p}
function keep(u,r){return r.clone().blob().then(function(b){return put(key(u),{b:b,t:r.headers.get('content-type')||''})})}
self.addEventListener('install',function(e){e.waitUntil(Promise.all(SHELL.map(function(u){return fetch(u,{cache:'reload'}).then(function(r){if(r.ok)return keep(new URL(u,self.registration.scope).href,r)}).catch(function(){})})));self.skipWaiting()});
self.addEventListener('activate',function(e){e.waitUntil(self.clients.claim())});
self.addEventListener('fetch',function(e){
  var q=e.request,u=new URL(q.url);
  if(q.method!=='GET'||u.origin!==location.origin||u.pathname.indexOf(new URL(self.registration.scope).pathname)!==0||u.pathname.indexOf('/audio/')>=0)return;
  e.respondWith(fetch(q,{cache:'no-cache'}).then(function(r){if(r&&r.ok&&r.type==='basic')keep(q.url,r);return r}).catch(function(){
    return get(key(q.url)).then(function(v){
      if(!v&&q.mode==='navigate')return get('index.html');return v}).then(function(v){
      return v?new Response(v.b,{headers:{'content-type':v.t}}):Response.error()})}));
});
