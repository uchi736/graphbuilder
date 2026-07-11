import { DropdownButton, Menu } from '@neo4j-ndl/react';
import { useRef, useState } from 'react';
import TooltipWrapper from '../UI/TipWrapper';
import { useFileContext } from '../../context/UsersFiles';
interface SchemaDropdownProps {
  isDisabled: boolean;
  onSchemaSelect?: (source: string, nodes: any[], rels: any[]) => void; // <-- NEW
}
const SchemaDropdown: React.FunctionComponent<SchemaDropdownProps> = ({ isDisabled, onSchemaSelect }) => {
  const [isOpen, setIsOpen] = useState(false);
  const btnRef = useRef(null);
  const {
    userDefinedNodes,
    userDefinedRels,
    dbNodes,
    dbRels,
    schemaValNodes,
    schemaValRels,
    preDefinedNodes,
    preDefinedRels,
  } = useFileContext();
  const handleSelect = (source: string, nodes: any[], rels: any[]) => {
    setIsOpen(false);
    if (onSchemaSelect) {
      onSchemaSelect(source, nodes, rels);
    }
  };
  return (
    <div className='relative'>
      <DropdownButton
        isOpen={isOpen}
        ref={btnRef}
        isDisabled={isDisabled}
        htmlAttributes={{
          onClick: (e) => {
            e.preventDefault();
            setIsOpen((prev) => !prev);
          },
        }}
      >
        スキーマの表示元 ...
      </DropdownButton>
      <Menu isOpen={isOpen} anchorRef={btnRef} onClose={() => setIsOpen(false)}>
        <Menu.Items htmlAttributes={{ id: 'default-menu' }}>
          <Menu.Item
            title={
              <TooltipWrapper placement='right' tooltip={'定義済みスキーマ'}>
                定義済みスキーマ
              </TooltipWrapper>
            }
            onClick={() => handleSelect('predefined', preDefinedNodes, preDefinedRels)}
          />
          <Menu.Item
            title={
              <TooltipWrapper placement='right' tooltip={'DBスキーマ'}>
                DBスキーマ
              </TooltipWrapper>
            }
            onClick={() => handleSelect('db', dbNodes, dbRels)}
          />
          <Menu.Item
            title={
              <TooltipWrapper placement='right' tooltip={'テキストからスキーマを取得'}>
                テキストからスキーマを取得
              </TooltipWrapper>
            }
            onClick={() => handleSelect('text', schemaValNodes, schemaValRels)}
          />
          <Menu.Item
            title={
              <TooltipWrapper placement='right' tooltip={'ユーザー定義'}>
                ユーザー定義
              </TooltipWrapper>
            }
            onClick={() => handleSelect('user', userDefinedNodes, userDefinedRels)}
          />
        </Menu.Items>
      </Menu>
    </div>
  );
};
export default SchemaDropdown;
