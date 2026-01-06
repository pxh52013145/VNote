import { Switch } from '@/components/ui/switch'
import { FC } from 'react'
import styles from './index.module.css'
import { useNavigate, useParams } from 'react-router-dom'
import AILogo from '@/components/Form/modelForm/Icons'
import { useProviderStore } from '@/store/providerStore'
import toast from 'react-hot-toast'
import { Trash2 } from 'lucide-react'
export interface IProviderCardProps {
  id: string
  providerName: string
  Icon: string
  enable: number
  type: string
}
const ProviderCard: FC<IProviderCardProps> = ({
  providerName,
  Icon,
  id,
  enable,
  type,
}: IProviderCardProps) => {
  const navigate = useNavigate()
  const updateProvider = useProviderStore(state => state.updateProvider)
  const deleteProvider = useProviderStore(state => state.deleteProvider)
  const handleClick = () => {
    navigate(`/settings/model/${id}`)
  }
  const handleEnable = () => {
    updateProvider({
      id,
      enabled: enable == 1 ? 0 : 1,
    })
  }
  // @ts-ignore
  const { id: currentId } = useParams()
  const isActive = currentId === id

  const handleDelete = async () => {
    if (type === 'built-in') return
    if (!window.confirm('确定要删除这个自定义供应商吗？')) return
    try {
      await deleteProvider(id)
      toast.success('删除成功')
      if (currentId === id) {
        navigate('/settings/model')
      }
    } catch (error) {
      console.error(error)
    }
  }
  return (
    <div
      onClick={() => {
        handleClick()
      }}
      className={
        styles.card +
        ' flex h-14 items-center justify-between rounded border border-[#f3f3f3] p-2' +
        (isActive ? ' bg-[#F0F0F0] font-semibold text-blue-600' : '')
      }
    >
      <div className="flex items-center text-lg">
        <div className="flex h-9 w-9 items-center">
          <AILogo name={Icon} />
        </div>
        <div className="font-semibold">{providerName}</div>
      </div>

      <div>
        {type !== 'built-in' && (
          <button
            type="button"
            className="mr-2 inline-flex h-8 w-8 items-center justify-center rounded text-red-500 hover:bg-red-50"
            title="删除"
            onClick={e => {
              e.preventDefault()
              e.stopPropagation()
              handleDelete()
            }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
        <Switch
          onClick={e => {
            e.preventDefault()
            e.stopPropagation()
            handleEnable()
          }}
          checked={enable == 1}
        />
      </div>
    </div>
  )
}
export default ProviderCard
