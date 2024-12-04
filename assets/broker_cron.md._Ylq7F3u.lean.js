import{_ as a,c as n,a4 as o,o as e}from"./chunks/framework.qvkBPMhT.js";const D=JSON.parse('{"title":"CronBroker","description":"","frontmatter":{"outline":"deep","title":"CronBroker"},"headers":[],"relativePath":"broker/cron.md","filePath":"broker/cron.md","lastUpdated":1691570666000}'),p={name:"broker/cron.md"};function l(r,s,t,c,i,y){return e(),n("div",null,s[0]||(s[0]=[o(`<h2 id="cronbroker" tabindex="-1">CronBroker <a class="header-anchor" href="#cronbroker" aria-label="Permalink to &quot;CronBroker&quot;">​</a></h2><p><code>CronBroker</code>是一个定时自执行的Broker，它允许你通过配置<a href="https://github.com/kiorky/croniter" target="_blank" rel="noreferrer">Cron表达式</a>来自动执行一些任务。</p><div class="language-python"><button title="Copy Code" class="copy"></button><span class="lang">python</span><pre class="shiki vitesse-dark vp-code" tabindex="0"><code><span class="line"><span style="color:#4D9375;">from</span><span style="color:#DBD7CAEE;"> onestep </span><span style="color:#4D9375;">import</span><span style="color:#DBD7CAEE;"> step</span><span style="color:#666666;">,</span><span style="color:#DBD7CAEE;"> CronBroker</span></span>
<span class="line"></span>
<span class="line"></span>
<span class="line highlighted"><span style="color:#666666;">@</span><span style="color:#80A665;">step</span><span style="color:#666666;">(</span><span style="color:#BD976A;">from_broker</span><span style="color:#666666;">=</span><span style="color:#DBD7CAEE;">CronBroker</span><span style="color:#666666;">(</span><span style="color:#C98A7D77;">&quot;</span><span style="color:#C98A7D;">* * * * * */3</span><span style="color:#C98A7D77;">&quot;</span><span style="color:#666666;">,</span><span style="color:#BD976A;"> name</span><span style="color:#666666;">=</span><span style="color:#C98A7D77;">&quot;</span><span style="color:#C98A7D;">miclon</span><span style="color:#C98A7D77;">&quot;</span><span style="color:#666666;">))</span></span>
<span class="line"><span style="color:#CB7676;">def</span><span style="color:#80A665;"> cron_task</span><span style="color:#666666;">(</span><span style="color:#DBD7CAEE;">message</span><span style="color:#666666;">):</span></span>
<span class="line"><span style="color:#B8A965;">    print</span><span style="color:#666666;">(</span><span style="color:#DBD7CAEE;">message</span><span style="color:#666666;">)</span></span>
<span class="line"><span style="color:#4D9375;">    return</span><span style="color:#DBD7CAEE;"> message</span></span>
<span class="line"></span>
<span class="line"></span>
<span class="line"><span style="color:#4D9375;">if</span><span style="color:#B8A965;"> __name__</span><span style="color:#CB7676;"> ==</span><span style="color:#C98A7D77;"> &#39;</span><span style="color:#C98A7D;">__main__</span><span style="color:#C98A7D77;">&#39;</span><span style="color:#666666;">:</span></span>
<span class="line"><span style="color:#DBD7CAEE;">    step</span><span style="color:#666666;">.</span><span style="color:#DBD7CAEE;">start</span><span style="color:#666666;">(</span><span style="color:#BD976A;">block</span><span style="color:#666666;">=</span><span style="color:#4D9375;">True</span><span style="color:#666666;">)</span></span></code></pre></div><p>上述代码会每隔3秒执行一次<code>cron_task</code>，并且会将<code>name</code>的值传递给消息体。</p><div class="language-text"><button title="Copy Code" class="copy"></button><span class="lang">text</span><pre class="shiki vitesse-dark vp-code" tabindex="0"><code><span class="line"><span>{&#39;body&#39;: {&#39;name&#39;: &#39;miclon&#39;}, &#39;extra&#39;: {&#39;task_id&#39;: &#39;99c73c21-6473-4be5-8836-bfc0ddcdf8c3&#39;, &#39;publish_time&#39;: 1691546688.498, &#39;failure_count&#39;: 0}}</span></span>
<span class="line"><span>{&#39;body&#39;: {&#39;name&#39;: &#39;miclon&#39;}, &#39;extra&#39;: {&#39;task_id&#39;: &#39;ba79bd1a-fc60-46d8-8a44-999f715729f0&#39;, &#39;publish_time&#39;: 1691546691.505, &#39;failure_count&#39;: 0}}</span></span></code></pre></div>`,5)]))}const C=a(p,[["render",l]]);export{D as __pageData,C as default};
