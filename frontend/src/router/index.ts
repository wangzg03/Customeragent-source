import { createRouter, createWebHistory } from 'vue-router'
const Home = () => import('../views/Home.vue')
const Evaluation = () => import('../views/Evaluation.vue')
const Records = () => import('../views/Records.vue')
const Workbench = () => import('../features/workbench/Workbench.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: Workbench
    },
    {
      path: '/legacy',
      name: 'LegacyHome',
      component: Home
    },
    {
      path: '/evaluation',
      name: 'Evaluation',
      component: Evaluation
    },
    {
      path: '/records',
      name: 'Records',
      component: Records
    }
  ]
})

export default router
