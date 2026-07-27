interface PanelHeaderProps {
  title: string
  subtitle?: string
}

function PanelHeader({ title, subtitle }: PanelHeaderProps) {
  return (
    <>
      <h2>{title}</h2>
      {subtitle && <p className="panel-subtitle">{subtitle}</p>}
    </>
  )
}

export default PanelHeader
