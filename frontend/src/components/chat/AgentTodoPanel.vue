<template>
  <section
    class="agent-todo-panel"
    aria-label="Agent todo"
  >
    <div class="todo-header">
      <div class="todo-title">
        <span
          :class="['todo-status-mark', { complete: isComplete }]"
          aria-hidden="true"
        />
        <span>Update Todos</span>
      </div>
      <span
        :class="['todo-count', { complete: isComplete }]"
      >
        {{ completed }} / {{ total }}
      </span>
    </div>

    <ol class="todo-list">
      <li
        v-for="(item, index) in items"
        :key="item.id || `${index}-${item.content}`"
        :class="['todo-item', { done: item.status === 'completed' }]"
      >
        <span
          class="todo-check"
          aria-hidden="true"
        />
        <span class="todo-content">{{ item.content }}</span>
      </li>
    </ol>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  todoSnapshot: { type: Object, required: true },
})

const items = computed(() => (
  Array.isArray(props.todoSnapshot.items) ? props.todoSnapshot.items : []
))
const total = computed(() => Number(props.todoSnapshot.total ?? items.value.length))
const completed = computed(() => Number(
  props.todoSnapshot.completed ?? items.value.filter((item) => item.status === 'completed').length,
))
const isComplete = computed(() => items.value.length > 0 && completed.value >= total.value)
</script>

<style scoped>
.agent-todo-panel {
  width: 100%;
  max-width: 900px;
  margin: 8px auto 14px;
  padding: 2px 0 4px;
  color: var(--t3);
  font-family: var(--sans);
  font-size: 13px;
}

.todo-header {
  width: auto;
  max-width: 100%;
  min-height: 24px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.todo-title {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--t1);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0;
}

.todo-status-mark {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 50%;
  background: var(--acc2);
  box-shadow: 0 0 10px rgba(158, 191, 255, 0.34);
}

.todo-status-mark.complete {
  background: var(--ok);
  box-shadow: 0 0 10px rgba(143, 216, 199, 0.34);
}

.todo-count {
  flex: 0 0 auto;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1;
}

.todo-count.complete {
  color: var(--ok);
}

.todo-list {
  display: grid;
  gap: 6px;
  list-style: none;
  padding-left: 27px;
}

.todo-item {
  min-width: 0;
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  color: var(--t2);
  font-size: 13px;
  line-height: 1.55;
  transition: color 0.12s ease;
}

.todo-check {
  width: 13px;
  height: 13px;
  border: 1px solid rgba(185, 190, 196, 0.38);
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.025);
}

.todo-item.done {
  color: var(--t3);
}

.todo-item.done .todo-check {
  border-color: rgba(143, 216, 199, 0.45);
  background:
    linear-gradient(135deg, transparent 0 35%, rgba(24, 24, 24, 0.65) 35% 46%, transparent 46%),
    var(--ok-bg);
  position: relative;
}

.todo-item.done .todo-check::after {
  content: "";
  position: absolute;
  left: 3px;
  top: 1px;
  width: 5px;
  height: 8px;
  border: solid var(--ok);
  border-width: 0 1.5px 1.5px 0;
  transform: rotate(45deg);
}

.todo-item.done .todo-content {
  text-decoration: line-through;
  text-decoration-color: rgba(143, 216, 199, 0.42);
}

.todo-content {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
