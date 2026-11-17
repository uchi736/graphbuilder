import { Popover } from '@neo4j-ndl/react';
import { GraphEditContextMenuRequest } from '../../../types';
import { GRAPH_EDIT_POPOVER_Z_INDEX } from '../../../utils/GraphEditUtils';

export interface GraphEditMenuItem {
  id: string;
  title: string;
  description?: string;
  isDisabled?: boolean;
  isDanger?: boolean;
  onClick: () => void;
}

// ノードとリレーションの右クリックの操作の一覧。NDL の Popover を anchorPosition（クリックした位置）で開く。
// グラフ表示のダイアログは focus を閉じ込める（react-focus-lock）ので、中身を data-no-focus-lock で外す。
export default function GraphEditContextMenu({
  request,
  title,
  note,
  items,
  onClose,
}: {
  request: GraphEditContextMenuRequest;
  title: string;
  note?: string;
  items: GraphEditMenuItem[];
  onClose: () => void;
}) {
  return (
    <Popover
      isOpen={true}
      anchorPosition={{ x: request.x, y: request.y }}
      placement='bottom-start-top-start'
      offset={4}
      role='menu'
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          onClose();
        }
      }}
    >
      <Popover.Content className='p-1' style={{ zIndex: GRAPH_EDIT_POPOVER_Z_INDEX, minWidth: 260, maxWidth: 360 }}>
        <div data-no-focus-lock='true' className='flex flex-col'>
          <div className='px-3 pt-1 pb-1 n-body-small n-text-palette-neutral-text-weak textellipsis' title={title}>
            {title}
          </div>
          {note && <div className='px-3 pb-1 n-body-small n-text-palette-warning-text'>{note}</div>}
          {items.map((item) => (
            <button
              key={item.id}
              type='button'
              role='menuitem'
              disabled={item.isDisabled}
              className={`flex flex-col items-start w-full text-left px-3 py-1.5 rounded border-0 bg-transparent ${
                item.isDisabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:n-bg-palette-neutral-hover'
              } ${item.isDanger && !item.isDisabled ? 'n-text-palette-danger-text' : ''}`}
              onClick={() => {
                onClose();
                item.onClick();
              }}
            >
              <span className='n-body-medium'>{item.title}</span>
              {item.description && (
                <span className='n-body-small n-text-palette-neutral-text-weak'>{item.description}</span>
              )}
            </button>
          ))}
        </div>
      </Popover.Content>
    </Popover>
  );
}
