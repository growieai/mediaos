"use client";
import { useEffect, useRef } from "react";
import Icon from "./Icons";
export default function Modal({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current; if (dialog && !dialog.open) dialog.showModal(); return () => dialog?.close(); }, []);
  return <dialog ref={ref} className={`studio-modal ${wide ? "wide" : ""}`} onCancel={event => { event.preventDefault(); onClose(); }} aria-label={title}>
    <div className="modal-top"><span className="eyebrow">MEDIA OS / CREATOR STUDIO</span><button className="icon-button" aria-label={`Close ${title}`} onClick={onClose}><Icon name="close"/></button></div>
    {children}
  </dialog>;
}
