<script setup lang="ts">

const projectInfo = [
  {
    "href": "https://github.com/mic1on/onestep/actions/workflows/test.yml?query=event%3Apush+branch%3Amain",
    "src": "https://github.com/mic1on/onestep/workflows/test%20suite/badge.svg?branch=main&event=push",
    "alt": "test suite"
  },
  {
    "href": "https://pypi.org/project/onestep",
    "src": "https://img.shields.io/pypi/v/onestep.svg",
    "alt": "pypi version"
  },
  {
    "href": "https://pypi.org/project/onestep",
    "src": "https://img.shields.io/pypi/pyversions/onestep.svg",
    "alt": "python versions"
  }
]
</script>

<template>
  <div class="mt-5">
    <a v-for="project in projectInfo" :href="project.href" target="_blank" class="mr-2">
      <img :src="project.src" :alt="project.alt" class="inline">
    </a>
  </div>
</template>

<style scoped>
</style>
