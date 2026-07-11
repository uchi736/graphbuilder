import { Dropzone, Flex, Typography } from '@neo4j-ndl/react';
import { useState } from 'react';
import { IconButtonWithToolTip } from '../../../UI/IconButtonToolTip';
import { InformationCircleIconOutline } from '@neo4j-ndl/react/icons';
import { showErrorToast } from '../../../../utils/Toasts';
import { buttonCaptions } from '../../../../utils/Constants';
import Loader from '../../../../utils/Loader';

interface GraphSchema {
  nodeLabels: any[];
  relationshipTypes: any[];
  relationshipObjectTypes: any[];
  nodeObjectTypes: any[];
}
interface UploadJsonDataProps {
  onSchemaExtracted: (schema: GraphSchema) => void;
}
const UploadJsonData = ({ onSchemaExtracted }: UploadJsonDataProps) => {
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const onDropHandler = async (files: Partial<globalThis.File>[]) => {
    const file = files[0];
    if (!file) {
      return;
    }
    setIsLoading(true);
    try {
      const fileReader = new FileReader();
      fileReader.onload = (event) => {
        try {
          const jsonText = event.target?.result as string;
          const parsed = JSON.parse(jsonText);
          const graphSchema = parsed?.dataModel?.graphSchemaRepresentation?.graphSchema;
          if (
            graphSchema &&
            Array.isArray(graphSchema.nodeLabels) &&
            Array.isArray(graphSchema.relationshipTypes) &&
            Array.isArray(graphSchema.relationshipObjectTypes) &&
            Array.isArray(graphSchema.nodeObjectTypes)
          ) {
            onSchemaExtracted({
              nodeLabels: graphSchema.nodeLabels,
              relationshipTypes: graphSchema.relationshipTypes,
              relationshipObjectTypes: graphSchema.relationshipObjectTypes,
              nodeObjectTypes: graphSchema.nodeObjectTypes,
            });
          } else {
            showErrorToast('graphSchemaの形式が不正です');
          }
        } catch (err) {
          console.error(err);
          showErrorToast('JSONファイルの解析に失敗しました。');
        } finally {
          setIsLoading(false);
        }
      };
      fileReader.readAsText(file as File);
    } catch (err) {
      console.error(err);
      showErrorToast('ファイルの読み取り中にエラーが発生しました。');
      setIsLoading(false);
    }
  };
  return (
    <Dropzone
      loadingComponent={isLoading && <Loader title='アップロード中' />}
      isTesting={true}
      className='bg-none! dropzoneContainer'
      supportedFilesDescription={
        <Typography variant='body-small'>
          <Flex>
            <span>{buttonCaptions.importDropzoneSpan}</span>
            <div className='align-self-center'>
              <IconButtonWithToolTip
                label='ソース情報'
                clean
                text={
                  <Typography variant='body-small'>
                    <Flex gap='3' alignItems='flex-start'>
                      <span>JSON (.json)</span>
                    </Flex>
                  </Typography>
                }
              >
                <InformationCircleIconOutline className='w-[22px] h-[22px]' />
              </IconButtonWithToolTip>
            </div>
          </Flex>
        </Typography>
      }
      dropZoneOptions={{
        accept: {
          'application/json': ['.json'],
        },
        onDrop: onDropHandler,
        onDropRejected: (e) => {
          if (e.length) {
            showErrorToast('アップロードに失敗しました。サポートされていない拡張子です。');
          }
        },
      }}
    />
  );
};
export default UploadJsonData;
