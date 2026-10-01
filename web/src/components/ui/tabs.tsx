import * as T from '@radix-ui/react-tabs'
import { cn } from '@/lib/utils'

export const Tabs = T.Root
export const TabsList = ({ className, ...p }: T.TabsListProps) => <T.List className={cn('inline-flex gap-1 rounded-md bg-raised p-0.5', className)} {...p} />
export const TabsTrigger = ({ className, ...p }: T.TabsTriggerProps) => (
  <T.Trigger className={cn('rounded-sm px-2.5 py-1 text-[12.5px] text-muted data-[state=active]:bg-bg data-[state=active]:text-ink', className)} {...p} />
)
export const TabsContent = T.Content
