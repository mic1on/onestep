<script setup lang="ts">
import {
  VPTeamPageTitle,
  VPTeamMembers,
} from 'vitepress/theme'
const members = [
  {
    avatar: 'https://www.github.com/mic1on.png',
    name: 'MicLon',
    title: 'Creator',
    links: [
      { icon: 'github', link: 'https://github.com/mic1on' },
    ]
  },
  {
    avatar: 'https://www.github.com/NowAnti.png',
    name: 'NowAnti',
    title: 'Collaborator',
    links: [
      { icon: 'github', link: 'https://github.com/NowAnti' },
    ]
  },
]
</script>

<template>
  <div class="content">
    <div class="content-container">
      <main class="main">
        <div class="vp-doc" flex flex-col items-center mt-10>
          <VPTeamPageTitle>
            <template #title>
              所有贡献者
            </template>
          </VPTeamPageTitle>
          <VPTeamMembers :members="members" />
        </div>
      </main>
    </div>
  </div>
</template>
