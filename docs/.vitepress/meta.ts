// noinspection ES6PreferShortImport: IntelliJ IDE hint to avoid warning to use `~/contributors`, will fail on build if changed

/* Texts */
export const vitestName = 'OneStep'
export const vitestShortName = 'OneStep'
export const vitestDescription = '仅需一步，轻松实现分布式异步任务。'

/* CDN fonts and styles */
export const googleapis = 'https://fonts.googleapis.com'
export const gstatic = 'https://fonts.gstatic.com'
export const font = `${googleapis}/css2?family=Readex+Pro:wght@200;400;600&display=swap`

/* vitepress head */
export const ogUrl = 'https://onestep.code05.com/'
export const ogImage = `${ogUrl}logo.png`

/* GitHub and social links */
export const github = 'https://github.com/mic1on/onestep'
export const blog = 'https://code05.com'
export const releases = 'https://github.com/mic1on/onestep/releases'

/* Avatar/Image/Sponsors servers */
export const preconnectLinks = [googleapis, gstatic]
export const preconnectHomeLinks = [googleapis, gstatic]

/* PWA runtime caching urlPattern regular expressions */
export const pwaFontsRegex = new RegExp(`^${googleapis}/.*`, 'i')
export const pwaFontStylesRegex = new RegExp(`^${gstatic}/.*`, 'i')
// eslint-disable-next-line prefer-regex-literals
export const githubusercontentRegex = new RegExp('^https://((i.ibb.co)|((raw|user-images).githubusercontent.com))/.*', 'i')
