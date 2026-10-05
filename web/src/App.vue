<script setup>
import { ref, nextTick, onMounted } from 'vue'

const messages = ref([])        // {role, text, type, collapsed}
const sessionId = ref('')
const input = ref('')
const sending = ref(false)
const scrollBox = ref(null)

const EXAMPLES = [
  '明天西安什么天气',
  '今天十大热点',
  '本周AI热点有哪些（自动调研生成报告）',
  '讲个笑话（暂仅终端支持）',
]

function addUser(text) {
  messages.value.push({ role: 'user', text })
}
function addAgent(text, type = 'agent', collapsed = false) {
  messages.value.push({ role: 'agent', text, type, collapsed })
}
function handleEvent(ev) {
  if (ev.type === 'done') {
    sending.value = false
    if (ev.data && ev.data.session_id) sessionId.value = ev.data.session_id
    return
  }
  if (ev.type === 'plan') {
    addAgent(ev.text, 'plan', true)          // 执行计划：默认折叠
  } else if (ev.type === 'progress') {
    addAgent(ev.text, 'progress', true)      // 运行过程：默认折叠
  } else if (ev.type === 'reflection') {
    addAgent(ev.text, 'reflection', false)
  } else if (ev.type === 'digest') {
    addAgent(ev.text, 'digest', false)       // 速递正文
  } else if (ev.type === 'summary') {
    addAgent(ev.text, 'summary', false)      // 会话摘要：灰色小字
  } else if (ev.type === 'error') {
    addAgent(ev.text, 'error', false)
  } else {
    addAgent(ev.text, 'agent', false)
  }
  scrollBottom()
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  input.value = ''
  sending.value = true
  addUser(text)
  scrollBottom()
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId.value, message: text }),
    })
    if (!resp.ok) {
      addAgent('请求失败：HTTP ' + resp.status, 'error')
      sending.value = false
      return
    }
    // SSE 流式读取（POST 流，用 fetch ReadableStream 解析）
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        for (const line of block.split('\n')) {
          if (line.startsWith('data: ')) {
            try { handleEvent(JSON.parse(line.slice(6))) } catch (e) { /* 忽略解析失败 */ }
          }
        }
      }
    }
  } catch (e) {
    addAgent('网络错误：' + e.message, 'error')
  }
  sending.value = false
}

function newChat() {
  sessionId.value = ''
  messages.value = []
  sending.value = false
}

function toggle(m) { m.collapsed = !m.collapsed }

function scrollBottom() {
  nextTick(() => { if (scrollBox.value) scrollBox.value.scrollTop = scrollBox.value.scrollHeight })
}

onMounted(() => {
  // 聚焦输入框
  document.getElementById('chat-input')?.focus()
})
</script>

<template>
  <div class="wrap">
    <header class="topbar">
      <div class="brand">
        <span class="logo">HN</span>
        <div>
          <div class="title">HotNews · 对话式资讯调研</div>
          <div class="subtitle">热点调研 / 热榜 / 天气 / 日期 · DeepSeek 驱动</div>
        </div>
      </div>
      <button class="new-btn" @click="newChat">新会话</button>
    </header>

    <main ref="scrollBox" class="chatbox">
      <div v-if="!messages.length" class="empty">
        <p>输入问题开始对话，例如：</p>
        <div class="examples">
          <span v-for="(ex, i) in EXAMPLES" :key="i" class="ex" @click="input = ex">{{ ex }}</span>
        </div>
        <p class="tip">调研类任务会自动展示「执行计划」与「运行过程」，完成后生成 Markdown 报告。</p>
      </div>

      <template v-for="(m, i) in messages" :key="i">
        <!-- 用户消息 -->
        <div v-if="m.role === 'user'" class="row user">
          <div class="bubble user-b">{{ m.text }}</div>
        </div>

        <!-- Agent 消息 -->
        <div v-else class="row agent">
          <div :class="['bubble', 'agent-b', m.type]">
            <!-- 可折叠的 meta 块：计划 / 运行过程 -->
            <template v-if="m.type === 'plan' || m.type === 'progress'">
              <div class="meta-head" @click="toggle(m)">
                <span class="caret">{{ m.collapsed ? '▸' : '▾' }}</span>
                {{ m.type === 'plan' ? '执行计划' : '运行过程' }}
              </div>
              <pre v-if="!m.collapsed" class="meta-body">{{ m.text }}</pre>
              <div v-else class="hint">（点击展开）</div>
            </template>
            <!-- 速递正文 -->
            <pre v-else-if="m.type === 'digest'" class="digest">{{ m.text }}</pre>
            <!-- 普通/反思/摘要/错误 -->
            <template v-else>
              <span v-if="m.type === 'summary'" class="summary">{{ m.text }}</span>
              <pre v-else class="text">{{ m.text }}</pre>
            </template>
          </div>
        </div>
      </template>

      <div v-if="sending" class="row agent">
        <div class="bubble agent-b thinking">
          <span class="dot"></span> 正在处理，请稍候…
        </div>
      </div>
    </main>

    <footer class="inputbar">
      <input
        id="chat-input"
        v-model="input"
        placeholder="输入消息，Enter 发送"
        :disabled="sending"
        @keydown.enter="send"
      />
      <button class="send-btn" :disabled="sending || !input.trim()" @click="send">
        {{ sending ? '处理中' : '发送' }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; height: 100%; max-width: 860px; margin: 0 auto; }

.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; background: #fff; border-bottom: 1px solid #e5e7eb;
}
.brand { display: flex; align-items: center; gap: 12px; }
.logo {
  width: 40px; height: 40px; border-radius: 10px;
  background: linear-gradient(135deg, #0ea5a4, #2563eb);
  color: #fff; font-weight: 700; display: flex; align-items: center; justify-content: center;
}
.title { font-size: 16px; font-weight: 700; }
.subtitle { font-size: 12px; color: #6b7280; }
.new-btn { padding: 8px 16px; background: #eef2ff; color: #2563eb; font-weight: 600; }
.new-btn:hover { background: #e0e7ff; }

.chatbox { flex: 1; overflow-y: auto; padding: 20px; }

.empty { text-align: center; margin-top: 9vh; color: #6b7280; }
.examples { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin: 16px 0; }
.ex {
  padding: 8px 14px; background: #fff; border: 1px solid #e5e7eb; border-radius: 999px;
  font-size: 13px; cursor: pointer; color: #374151;
}
.ex:hover { border-color: #2563eb; color: #2563eb; }
.tip { font-size: 12px; color: #9ca3af; }

.row { display: flex; margin-bottom: 14px; }
.row.user { justify-content: flex-end; }
.row.agent { justify-content: flex-start; }

.bubble { max-width: 82%; padding: 10px 14px; border-radius: 12px; line-height: 1.6; }
.user-b { background: #2563eb; color: #fff; border-top-right-radius: 4px; }
.agent-b { background: #fff; border: 1px solid #e5e7eb; border-top-left-radius: 4px; }
.agent-b.error { border-color: #fca5a5; background: #fef2f2; }
.agent-b.digest { background: #f0fdf9; border-color: #99f6e4; }

.meta-head { font-size: 12px; color: #2563eb; cursor: pointer; font-weight: 600; user-select: none; }
.caret { display: inline-block; width: 14px; }
.meta-body { font-size: 12px; color: #6b7280; margin-top: 6px; white-space: pre-wrap; word-break: break-word; }
.hint { font-size: 12px; color: #9ca3af; margin-top: 4px; }

.digest { white-space: pre-wrap; word-break: break-word; font-size: 14px; }
.text { white-space: pre-wrap; word-break: break-word; font-size: 14px; font-family: inherit; }
.summary { font-size: 12px; color: #9ca3af; }

.thinking { color: #6b7280; font-size: 13px; }
.dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: #0ea5a4; margin-right: 6px;
  animation: pulse 1s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: .3; } 50% { opacity: 1; } }

.inputbar {
  display: flex; gap: 10px; padding: 14px 20px;
  background: #fff; border-top: 1px solid #e5e7eb;
}
.inputbar input {
  flex: 1; padding: 10px 14px; border: 1px solid #d1d5db; border-radius: 10px;
  font-size: 14px; outline: none;
}
.inputbar input:focus { border-color: #2563eb; }
.inputbar input:disabled { background: #f3f4f6; }
.send-btn { padding: 10px 22px; background: #2563eb; color: #fff; font-weight: 600; }
.send-btn:hover:not(:disabled) { background: #1d4ed8; }
.send-btn:disabled { background: #cbd5e1; cursor: not-allowed; }
</style>
