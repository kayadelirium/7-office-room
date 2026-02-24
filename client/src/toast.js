import { toast } from 'react-toastify'

export function showToast(msg, ok = true) {
  ok ? toast.success(msg) : toast.error(msg)
}
