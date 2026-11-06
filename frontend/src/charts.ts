import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, TitleComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
use([BarChart, GridComponent, LegendComponent, TooltipComponent, TitleComponent, CanvasRenderer])
export { init, graphic } from 'echarts/core'
export type { ECharts } from 'echarts/core'
export type { EChartsOption } from 'echarts'
