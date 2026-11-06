import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
let legacyComponentsLoaded = false
router.beforeEach(async (to) => {
  if (to.path !== '/' && !legacyComponentsLoaded) {
    const [components] = await Promise.all([import('element-plus'), import('element-plus/dist/index.css')])
    app.use(components.default)
    legacyComponentsLoaded = true
  }
})
app.use(router)

app.mount('#app')
