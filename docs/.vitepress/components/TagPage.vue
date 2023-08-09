<script setup lang="ts">
import { useArticles, useUrlParams } from '../composables'
import { ref } from 'vue';


function filerArticleByTag(articles: any[], tag: string) {
  return articles.filter((article) => {
    return article.tags && article.tags.includes(tag);
  });
}

const tagName = ref(useUrlParams(window.location.href).tag)

const articles = filerArticleByTag(
  useArticles(),
  tagName.value
)

</script>

<template>
  <div>
    标签为 <span class="font-bold text-[#6da13f]">{{ tagName }}</span> 的文章：
    <ul class="features-list" dir="auto" flex="~ col gap2 md:gap-3">
      <ListItem v-for="article in articles" :key="article.link">
        <a :href="article.link">{{ article.text }}</a>
      </ListItem>
    </ul>
  </div>
</template>
<style scoped>
.features-list li {
  list-style: none;
  display: flex;
  gap: 0.4rem;
  margin: 0;
  padding-top: 10px;
}

.features-list {
  padding: 0;
}
</style>
