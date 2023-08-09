<script setup lang="ts">
import { useRoute, useRouter } from 'vitepress';
import { useArticles } from '../composables'
import { ref, watch } from 'vue';

const articles = useArticles()
const route = useRoute()
const router = useRouter()

function filerArticleByLink(articles: any[], path: string) {
  return articles.filter(
    (article) => `${article.link}.html` === path
  )
}

const tags = ref<string[]>([])

function goToTagsPage(tag: string) {
  router.go(`/tags.html?tag=${tag}`)
}
watch(() => route.path, () => {
  const article = filerArticleByLink(
    articles,
    route.path
  )
  tags.value = (article && article[0] ? article[0].tags : []) || []

}, { immediate: true })

</script>

<template>
  <div v-if="tags.length > 0">
    Tags:
    <span @click="goToTagsPage(tag)" v-for="tag in tags" :key="tag" class="font-bold text-[#6da13f] cursor-pointer pr-3">
      {{ tag }}
    </span>
  </div>
</template>
