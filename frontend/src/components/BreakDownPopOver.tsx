import CustomPopOver from './UI/CustomPopOver';
import { InformationCircleIconOutline } from '@neo4j-ndl/react/icons';
import { CustomFileBase } from '../types';
import { useCredentials } from '../context/UserCredentials';

export default function BreakDownPopOver({ file, isNodeCount = true }: { file: CustomFileBase; isNodeCount: boolean }) {
  const { isGdsActive } = useCredentials();

  return (
    <CustomPopOver Trigger={<InformationCircleIconOutline className='n-size-token-6' />}>
      {isNodeCount ? (
        <ul className='p-2!'>
          <li>チャンクノード: {file.chunkNodeCount}</li>
          <li>エンティティノード: {file.entityNodeCount}</li>
          {isGdsActive && <li>コミュニティノード: {file.communityNodeCount}</li>}
        </ul>
      ) : (
        <ul className='p-2!'>
          <li>チャンクリレーション: {file.chunkRelCount}</li>
          <li>エンティティリレーション: {file.entityEntityRelCount}</li>
          {isGdsActive && <li>コミュニティリレーション: {file.communityRelCount}</li>}
        </ul>
      )}
    </CustomPopOver>
  );
}
