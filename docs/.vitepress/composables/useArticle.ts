import { useData } from 'vitepress';

export function useArticles() {
  const { theme } = useData()

  const { sidebar } = theme.value
  const articles: any[] = [];
  Object.keys(sidebar).forEach((dir) => {
    for (const item of sidebar[dir]) {
      item.items.forEach((route: any) =>
        articles.push({
          ...route,
          parentLink: dir,
          parentText: item.text,
          tags: route.tags
        })
      );
    }

  });
  return articles
}
