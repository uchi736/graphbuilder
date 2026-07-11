import { Box, Flex } from '@neo4j-ndl/react';
import Markdown from 'react-markdown';
import ButtonWithToolTip from '../../UI/ButtonWithToolTip';
import { useCredentials } from '../../../context/UserCredentials';
import { useState } from 'react';

export default function VectorIndexMisMatchAlert({
  recreateVectorIndex,
  isVectorIndexAlreadyExists,
  userVectorIndexDimension,
  chunksExists,
  applicationDimension,
}: {
  recreateVectorIndex: () => Promise<void>;
  isVectorIndexAlreadyExists: boolean;
  userVectorIndexDimension?: number;
  chunksExists: boolean;
  applicationDimension?: number;
}) {
  const { userCredentials } = useCredentials();
  const [vectorIndexLoading, setVectorIndexLoading] = useState<boolean>(false);

  return (
    <Flex>
      <Box>
        <div className='whitespace-pre-wrap'>
          <Markdown>
            {isVectorIndexAlreadyExists
              ? `**ベクトルインデックスの互換性がありません**
既存のNeo4jベクトルインデックスの次元数 (${userVectorIndexDimension}) は、このアプリケーションがサポートする次元数 (${applicationDimension}) と互換性がありません。
続行するには、次のいずれかを選択してください:
1.**ベクトルインデックスの再作成:** 「ベクトルインデックスを再作成」をクリックして互換性のあるベクトルインデックスを生成します。
2.**別のインスタンスの使用:** 互換性のあるベクトルインデックス構成を持つNeo4jインスタンスに接続します  `
              : chunksExists
                ? `ベクトルインデックスは、データ内で効率的な類似度検索を行うために不可欠です。これがないと、一部のチャンクは意味や文脈に基づくクエリから見えなくなります。ベクトルインデックスを作成することで、関連情報を素早く正確に見つけられるようになり、データの可能性を最大限に引き出せます。`
                : ''}
          </Markdown>
        </div>
      </Box>
      <div className='n-size-full n-flex n-flex-col n-items-center n-justify-center'>
        <ButtonWithToolTip
          text={
            userCredentials === null
              ? 'インデックスを作成する前に接続を確立してください'
              : 'サポートされているベクトルインデックスを作成します'
          }
          label='サポートされているベクトルインデックスを作成します'
          placement='top'
          loading={vectorIndexLoading}
          onClick={async () => {
            setVectorIndexLoading(true);
            await recreateVectorIndex();
            setVectorIndexLoading(false);
          }}
          className='w-full!'
          color='danger'
          disabled={userCredentials === null}
        >
          {isVectorIndexAlreadyExists ? 'ベクトルインデックスを再作成' : 'ベクトルインデックスを作成'}
        </ButtonWithToolTip>
      </div>
    </Flex>
  );
}
