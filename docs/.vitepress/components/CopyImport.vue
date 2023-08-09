<script setup lang="ts">
import { useClipboard } from '@vueuse/core'

const props = defineProps<{
  text: string
}>()

const { copy } = useClipboard()

const onCopy = () => {
  copy(props.text)
}

</script>

<template>
  <span @click="onCopy" class="text-size-3 !h-1 !w-1 bg-green-600 text-white p-1 rounded-md cursor-pointer">
    复制导入
  </span>
</template>

