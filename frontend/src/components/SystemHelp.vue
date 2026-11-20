<script setup>
import {
  ArrowLeft,
  CheckCircle2,
  Clapperboard,
  FileText,
  ScanLine,
  ShieldCheck,
  Sparkles,
  Upload,
  WandSparkles,
} from '@lucide/vue'

defineEmits(['back'])

const workflow = [
  {
    icon: Upload,
    title: '上传参考视频',
    detail: '系统读取视频时长、画面和声音，建立本次创作的基础。',
  },
  {
    icon: ScanLine,
    title: '理解视频结构',
    detail: '识别镜头、开场、卖点片段和结尾，保留原片的时间顺序。',
  },
  {
    icon: WandSparkles,
    title: '生成创意脚本',
    detail: '结合创作说明、产品信息和参考案例，生成三个不同角度的方案。',
  },
  {
    icon: Clapperboard,
    title: '确认后生成候选',
    detail: '确认脚本后，系统生成、混音并合成三条候选视频，可直接下载。',
  },
]

const scriptInputs = [
  '参考视频的画面与结构',
  '创作说明和产品卖点',
  '同类参考脚本',
  '创作规则与时间轴约束',
]
</script>

<template>
  <section class="help-page" data-testid="system-help-page">
    <div class="help-shell">
      <header class="help-hero">
        <div class="help-hero-copy">
          <span class="help-kicker">系统帮助</span>
          <h1>一条参考视频，如何变成可交付的新成片</h1>
          <p>
            系统负责理解视频、生成创意、控制流程和完成剪辑；你负责确认脚本并查看候选成片。
          </p>
          <div class="help-hero-actions">
            <button
              class="button subtle"
              type="button"
              data-testid="close-help"
              @click="$emit('back')"
            >
              <ArrowLeft :size="15" />
              返回工作台
            </button>
            <a class="help-jump-link" href="#system-flow">查看完整流程</a>
          </div>
        </div>
        <div class="help-principle">
          <span>核心原则</span>
          <strong>AI 提供创意</strong>
          <i></i>
          <strong>流程保证可控</strong>
        </div>
      </header>

      <section id="system-flow" class="help-section">
        <header class="help-section-head">
          <span>系统如何工作</span>
          <h2>四个步骤完成一条视频</h2>
          <p>AI 和媒体处理各司其职，关键节点保留人工确认。</p>
        </header>

        <div class="help-workflow">
          <article v-for="(step, index) in workflow" :key="step.title">
            <div class="help-step-line">
              <span class="help-step-icon">
                <component :is="step.icon" :size="19" />
              </span>
              <span class="help-step-number">{{ String(index + 1).padStart(2, '0') }}</span>
            </div>
            <strong>{{ step.title }}</strong>
            <p>{{ step.detail }}</p>
          </article>
        </div>

        <div class="help-tech-band">
          <div class="help-tech-intro">
            <span>技术实现</span>
            <strong>让创意可控，让结果可恢复</strong>
          </div>
          <div>
            <span>AI 模型</span>
            <p>理解视频内容、提取信息并生成创意与镜头方案。</p>
          </div>
          <div>
            <span>流程控制</span>
            <p>固定镜头顺序、时长和确认点，避免 AI 随意改写结构。</p>
          </div>
          <div>
            <span>媒体工具</span>
            <p>完成切片、生成、拼接、混音和字幕处理。</p>
          </div>
        </div>

        <figure class="help-figure" data-testid="help-diagram-system">
          <figcaption>
            <div>
              <strong>视频全链路全景</strong>
              <span>从上传到最终交付的完整步骤</span>
            </div>
            <a href="/help/system-overview.png" target="_blank" rel="noopener">查看大图</a>
          </figcaption>
          <a href="/help/system-overview.png" target="_blank" rel="noopener">
            <img
              src="/help/system-overview.png"
              alt="视频全链路技术方案，展示上传、理解、脚本、生成、选择和交付流程"
              width="1536"
              height="1024"
              loading="lazy"
              decoding="async"
            />
          </a>
        </figure>
      </section>

      <section id="script-generation" class="help-section help-script-section">
        <header class="help-section-head">
          <span>创意脚本</span>
          <h2>三份脚本，来自同一份真实创作上下文</h2>
          <p>不是随机写文案。系统先理解参考视频，再结合业务信息形成有依据的创意方向。</p>
        </header>

        <div class="help-script-layout">
          <div class="help-source-list">
            <span>AI 会参考</span>
            <ul>
              <li v-for="item in scriptInputs" :key="item">
                <CheckCircle2 :size="16" />
                {{ item }}
              </li>
            </ul>
          </div>
          <div class="help-script-output">
            <span>最终形成</span>
            <div>
              <b>1</b>
              <p><strong>创意意图</strong>明确目标、受众和核心卖点。</p>
            </div>
            <div>
              <b>2</b>
              <p><strong>创意骨架</strong>梳理开场、价值表达和行动引导。</p>
            </div>
            <div>
              <b>3</b>
              <p><strong>三份脚本</strong>分别从痛点、效果和信任角度展开。</p>
            </div>
          </div>
        </div>

        <div class="help-control-note">
          <ShieldCheck :size="21" />
          <div>
            <strong>时间轴由系统锁定，脚本由你确认</strong>
            <p>模型可以改写画面、口播和提示词，但不能改变镜头数量、顺序和原片结构。</p>
          </div>
        </div>

        <figure class="help-figure" data-testid="help-diagram-script">
          <figcaption>
            <div>
              <strong>创意脚本生成过程</strong>
              <span>从视频理解、卖点提取到三份候选脚本</span>
            </div>
            <a href="/help/creative-script-flow.png" target="_blank" rel="noopener">查看大图</a>
          </figcaption>
          <a href="/help/creative-script-flow.png" target="_blank" rel="noopener">
            <img
              src="/help/creative-script-flow.png"
              alt="创意脚本生成过程，展示创作上下文、视频理解、卖点提取、创意库和脚本输出"
              width="1536"
              height="1024"
              loading="lazy"
              decoding="async"
            />
          </a>
        </figure>
      </section>

      <section class="help-final">
        <div class="help-final-copy">
          <FileText :size="22" />
          <div>
            <h2>使用时可以这样理解</h2>
            <p>AI 提高创意和制作效率，系统负责守住结构与质量，你保留最终选择权。</p>
          </div>
        </div>
        <div class="help-facts">
          <div>
            <Sparkles :size="16" />
            <strong>脚本确认</strong>
            <span>确认脚本后才开始生成视频</span>
          </div>
          <div>
            <Clapperboard :size="16" />
            <strong>候选交付</strong>
            <span>三条候选成片可逐条下载，并与原视频同步对比</span>
          </div>
          <div>
            <ShieldCheck :size="16" />
            <strong>结果可追踪</strong>
            <span>失败可重试，原片重排可继续调整</span>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.help-page {
  min-height: 0;
  overflow-y: auto;
  color: var(--ink);
  background:
    linear-gradient(90deg, rgba(37, 99, 235, 0.035) 1px, transparent 1px) 0 0 / 72px 72px,
    var(--paper);
}

.help-shell {
  width: min(1180px, calc(100% - 48px));
  margin: 0 auto;
  padding: 0 0 64px;
}

.help-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 240px;
  gap: 54px;
  align-items: end;
  padding: 58px 0 46px;
}

.help-hero-copy {
  max-width: 780px;
}

.help-kicker {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 9px;
  color: var(--electric);
  font-size: 10px;
  font-weight: 700;
  background: #edf4ff;
  border: 1px solid #d6e4fa;
  border-radius: 5px;
}

.help-hero h1 {
  max-width: 760px;
  margin: 16px 0 14px;
  font-family: var(--font-display);
  font-size: clamp(32px, 4.5vw, 52px);
  font-weight: 720;
  line-height: 1.14;
}

.help-hero-copy > p {
  max-width: 680px;
  margin: 0;
  color: var(--ink-soft);
  font-size: 15px;
  line-height: 1.8;
}

.help-hero-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 24px;
}

.help-hero-actions .button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.help-jump-link {
  color: var(--electric);
  font-size: 11px;
  font-weight: 650;
  text-underline-offset: 4px;
}

.help-principle {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 22px;
  background: #1e2733;
  border-radius: 8px;
  box-shadow: 0 18px 44px rgba(30, 39, 51, 0.16);
}

.help-principle span {
  color: #aab6c5;
  font-size: 9px;
  font-weight: 700;
}

.help-principle strong {
  color: #ffffff;
  font-size: 14px;
  font-weight: 650;
}

.help-principle i {
  width: 28px;
  height: 1px;
  background: var(--gold);
}

.help-section {
  padding: 46px 0 50px;
  border-top: 1px solid var(--line);
}

.help-section-head {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr) minmax(260px, 0.72fr);
  gap: 24px;
  align-items: start;
  margin-bottom: 30px;
}

.help-section-head > span {
  padding-top: 5px;
  color: var(--electric);
  font-size: 10px;
  font-weight: 700;
}

.help-section-head h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 680;
  line-height: 1.25;
}

.help-section-head p {
  margin: 2px 0 0;
  color: var(--ink-muted);
  font-size: 12px;
  line-height: 1.75;
}

.help-workflow {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--line-strong);
}

.help-workflow article {
  position: relative;
  min-height: 190px;
  padding: 22px 24px 24px 0;
  border-right: 1px solid var(--line);
}

.help-workflow article:last-child {
  border-right: 0;
  padding-right: 0;
}

.help-workflow article:not(:first-child) {
  padding-left: 24px;
}

.help-step-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 22px;
}

.help-step-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  color: var(--electric);
  background: #ffffff;
  border: 1px solid #cbdcf6;
  border-radius: 8px;
  box-shadow: 0 7px 20px rgba(37, 99, 235, 0.08);
}

.help-step-number {
  color: #a5b0bf;
  font-family: var(--font-mono);
  font-size: 9px;
}

.help-workflow article > strong {
  display: block;
  font-size: 14px;
}

.help-workflow article > p {
  margin: 8px 0 0;
  color: var(--ink-muted);
  font-size: 11px;
  line-height: 1.75;
}

.help-tech-band {
  display: grid;
  grid-template-columns: minmax(170px, 0.8fr) repeat(3, minmax(0, 1fr));
  gap: 24px;
  margin-top: 22px;
  padding: 23px 24px;
  background: #edf4ff;
  border-top: 2px solid var(--electric);
}

.help-tech-intro,
.help-tech-band > div {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.help-tech-band span {
  color: #52709a;
  font-size: 9px;
  font-weight: 700;
}

.help-tech-intro strong {
  color: #183d72;
  font-size: 14px;
  line-height: 1.5;
}

.help-tech-band p {
  margin: 0;
  color: #52677f;
  font-size: 10px;
  line-height: 1.7;
}

.help-figure {
  margin: 30px 0 0;
  border: 1px solid var(--line);
  background: #ffffff;
}

.help-figure figcaption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
}

.help-figure figcaption > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.help-figure figcaption strong {
  font-size: 12px;
}

.help-figure figcaption span {
  color: var(--ink-muted);
  font-size: 10px;
}

.help-figure figcaption a {
  flex: 0 0 auto;
  color: var(--electric);
  font-size: 10px;
  font-weight: 650;
  text-underline-offset: 3px;
}

.help-figure > a {
  display: block;
  overflow: hidden;
  background: #f3f6fa;
}

.help-figure img {
  display: block;
  width: 100%;
  height: auto;
}

.help-script-layout {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr);
  gap: 42px;
}

.help-source-list > span,
.help-script-output > span {
  color: var(--ink-muted);
  font-size: 9px;
  font-weight: 700;
}

.help-source-list ul {
  display: flex;
  flex-direction: column;
  gap: 13px;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}

.help-source-list li {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12px;
  font-weight: 600;
}

.help-source-list li svg {
  flex: 0 0 auto;
  color: var(--success);
}

.help-script-output {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.help-script-output > span {
  grid-column: 1 / -1;
}

.help-script-output > div {
  padding-top: 13px;
  border-top: 2px solid var(--electric);
}

.help-script-output b {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  margin-bottom: 14px;
  color: #ffffff;
  font-family: var(--font-mono);
  font-size: 9px;
  background: var(--electric);
  border-radius: 50%;
}

.help-script-output p {
  margin: 0;
  color: var(--ink-muted);
  font-size: 11px;
  line-height: 1.75;
}

.help-script-output strong {
  display: block;
  margin-bottom: 5px;
  color: var(--ink);
  font-size: 12px;
}

.help-control-note {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  margin-top: 34px;
  padding: 18px 20px;
  background: #fffaef;
  border-left: 2px solid var(--gold);
}

.help-control-note svg {
  flex: 0 0 auto;
  color: #a87408;
}

.help-control-note strong {
  font-size: 12px;
}

.help-control-note p {
  margin: 5px 0 0;
  color: #6e6250;
  font-size: 10px;
  line-height: 1.7;
}

.help-final {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 48px;
  align-items: start;
  padding-top: 42px;
  border-top: 1px solid var(--line);
}

.help-final-copy {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.help-final-copy > svg {
  flex: 0 0 auto;
  color: var(--electric);
}

.help-final-copy h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 680;
}

.help-final-copy p {
  max-width: 500px;
  margin: 10px 0 0;
  color: var(--ink-muted);
  font-size: 12px;
  line-height: 1.8;
}

.help-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.help-facts > div {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-height: 112px;
  padding: 16px;
  background: #ffffff;
  border: 1px solid var(--line);
  border-radius: 7px;
}

.help-facts svg {
  margin-bottom: 4px;
  color: var(--electric);
}

.help-facts strong {
  font-size: 11px;
}

.help-facts span {
  color: var(--ink-muted);
  font-size: 9px;
  line-height: 1.6;
}

@media (max-width: 900px) {
  .help-shell {
    width: min(100% - 32px, 760px);
  }

  .help-hero {
    grid-template-columns: 1fr;
    gap: 26px;
    padding-top: 42px;
  }

  .help-principle {
    max-width: 320px;
  }

  .help-section-head {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .help-section-head > span {
    padding-top: 0;
  }

  .help-workflow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .help-workflow article:nth-child(2) {
    border-right: 0;
  }

  .help-workflow article:nth-child(n + 3) {
    border-top: 1px solid var(--line);
  }

  .help-tech-band {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .help-tech-intro {
    grid-column: 1 / -1;
  }

  .help-script-layout,
  .help-final {
    grid-template-columns: 1fr;
    gap: 28px;
  }
}

@media (max-width: 600px) {
  .help-shell {
    width: calc(100% - 24px);
  }

  .help-hero {
    padding: 32px 0 34px;
  }

  .help-hero h1 {
    font-size: 32px;
  }

  .help-hero-copy > p {
    font-size: 13px;
  }

  .help-section {
    padding: 34px 0 38px;
  }

  .help-section-head h2 {
    font-size: 23px;
  }

  .help-workflow {
    grid-template-columns: 1fr;
  }

  .help-workflow article,
  .help-workflow article:not(:first-child) {
    min-height: 0;
    padding: 18px 0;
    border-right: 0;
    border-top: 1px solid var(--line);
  }

  .help-workflow article:first-child {
    border-top: 0;
  }

  .help-step-line {
    margin-bottom: 12px;
  }

  .help-tech-band,
  .help-script-output,
  .help-facts {
    grid-template-columns: 1fr;
  }

  .help-tech-intro,
  .help-script-output > span {
    grid-column: auto;
  }

  .help-figure figcaption {
    align-items: flex-start;
    flex-direction: column;
  }

  .help-final {
    padding-top: 32px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .help-page * {
    scroll-behavior: auto;
  }
}
</style>
