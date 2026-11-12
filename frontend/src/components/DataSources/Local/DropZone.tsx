import { Button, Dropzone, Flex, SpotlightTarget, Typography } from '@neo4j-ndl/react';
import { useState, FunctionComponent, useEffect, useRef, ChangeEvent } from 'react';
import Loader from '../../../utils/Loader';
import { v4 as uuidv4 } from 'uuid';
import { useCredentials } from '../../../context/UserCredentials';
import { useFileContext } from '../../../context/UsersFiles';
import { CustomFile, CustomFileBase } from '../../../types';
import { buttonCaptions, chunkSize } from '../../../utils/Constants';
import { InformationCircleIconOutline } from '@neo4j-ndl/react/icons';
import { IconButtonWithToolTip } from '../../UI/IconButtonToolTip';
import { uploadAPI } from '../../../utils/FileAPI';
import { showErrorToast, showSuccessToast } from '../../../utils/Toasts';
import { getEmbeddingModel } from '../../../utils/EmbeddingConfigUtils';
import ReplaceConfirmDialog from '../../Popups/ReplaceConfirm';

const DropZone: FunctionComponent = () => {
  const { filesData, setFilesData, model } = useFileContext();
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isClicked, setIsClicked] = useState<boolean>(false);
  const { userCredentials } = useCredentials();
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const folderInputRef = useRef<HTMLInputElement>(null);
  // フォルダを選ぶ: フォルダ内の対応ファイルだけを、ドロップしたときと同じ経路に渡す
  const SUPPORTED_EXT = ['pdf', 'jpeg', 'jpg', 'png', 'svg', 'html', 'docx', 'txt', 'pptx', 'xls', 'md', 'xlsx'];
  const onFolderSelected = (e: ChangeEvent<HTMLInputElement>) => {
    const all = Array.from(e.target.files ?? []);
    const picked = all.filter((f) => SUPPORTED_EXT.includes(f.name.substring(f.name.lastIndexOf('.') + 1).toLowerCase()));
    e.target.value = '';
    if (!picked.length) {
      showErrorToast('フォルダに対応ファイルがありません');
      return;
    }
    if (picked.length < all.length) {
      showSuccessToast(`${picked.length} 件を受け付けました（対応外 ${all.length - picked.length} 件は除外）`);
    }
    onDropHandler(picked);
  };
  // 取り込み済み（Completed）の文書と同じ名前のファイルは、前の版を置き換えることを確かめてから追加する（C8）
  const [pendingDrop, setPendingDrop] = useState<{ files: Partial<globalThis.File>[]; replacing: string[] } | null>(
    null
  );
  const onDropHandler = (f: Partial<globalThis.File>[]) => {
    // 処理中・アップロード中・待機中の文書と同じ名前は受け付けない（アップロードで文書の状態が New に戻り、走っている抽出と混ざる）
    const busy = f.filter((x) =>
      filesData.some((r) => r.name === x.name && ['Processing', 'Uploading', 'Waiting'].includes(r.status))
    );
    if (busy.length) {
      showErrorToast(
        `処理中の文書と同じ名前のファイルは追加できません（${busy.map((x) => x.name).join('、')}）。処理が完了してから追加してください`
      );
    }
    const files = f.filter((x) => !busy.includes(x));
    const replacing = files
      .filter((x) => filesData.some((r) => r.name === x.name && r.status === 'Completed'))
      .map((x) => x.name as string);
    if (replacing.length) {
      setPendingDrop({ files, replacing });
    } else if (files.length) {
      addDroppedFiles(files);
    }
  };
  const addDroppedFiles = (f: Partial<globalThis.File>[]) => {
    setIsClicked(true);
    setSelectedFiles(f.map((f) => f as File));
    setIsLoading(false);
    if (f.length) {
      const defaultValues: CustomFileBase = {
        processingTotalTime: 0,
        status: 'None',
        nodesCount: 0,
        relationshipsCount: 0,
        model: model,
        fileSource: 'local file',
        uploadProgress: 0,
        processingProgress: undefined,
        retryOptionStatus: false,
        retryOption: '',
        chunkNodeCount: 0,
        chunkRelCount: 0,
        entityNodeCount: 0,
        entityEntityRelCount: 0,
        communityNodeCount: 0,
        communityRelCount: 0,
        createdAt: new Date(),
        token_usage: 0,
        embedding_model: getEmbeddingModel(),
      };

      const copiedFilesData: CustomFile[] = [...filesData];
      for (let index = 0; index < f.length; index++) {
        const file = f[index];
        const filedataIndex = copiedFilesData.findIndex((filedataitem) => filedataitem?.name === file?.name);
        if (filedataIndex == -1) {
          copiedFilesData.unshift({
            name: file.name,
            // @ts-ignore
            type: `${file.name.substring(file.name.lastIndexOf('.') + 1, file.name.length).toUpperCase()}`,
            size: file.size,
            uploadProgress: file.size && file?.size < chunkSize ? 100 : 0,
            id: uuidv4(),
            ...defaultValues,
          });
        } else {
          const tempFileData = copiedFilesData[filedataIndex];
          copiedFilesData.splice(filedataIndex, 1);
          copiedFilesData.unshift({
            ...tempFileData,
            status: defaultValues.status,
            nodesCount: defaultValues.nodesCount,
            relationshipsCount: defaultValues.relationshipsCount,
            processingTotalTime: defaultValues.processingTotalTime,
            model: defaultValues.model,
            fileSource: defaultValues.fileSource,
            processingProgress: defaultValues.processingProgress,
            token_usage: defaultValues.token_usage,
            embedding_model: defaultValues.embedding_model,
            // 再処理の指定を外し、大きさを新しい版にする（指定が残ると前のチャンクを使う再処理になり、新しい版が読まれない。C8）
            retryOption: defaultValues.retryOption,
            retryOptionStatus: defaultValues.retryOptionStatus,
            size: file.size,
          });
        }
      }
      setFilesData(copiedFilesData);
    }
  };
  useEffect(() => {
    if (selectedFiles.length > 0) {
      for (let index = 0; index < selectedFiles.length; index++) {
        const file = selectedFiles[index];
        if (filesData[index]?.status == 'None' && isClicked) {
          uploadFileInChunks(file);
        }
      }
    }
  }, [selectedFiles]);

  const uploadFileInChunks = (file: File) => {
    const totalChunks = Math.ceil(file.size / chunkSize);
    const chunkProgressIncrement = 100 / totalChunks;
    let chunkNumber = 1;
    let start = 0;
    let end = chunkSize;
    const uploadNextChunk = async () => {
      if (chunkNumber <= totalChunks) {
        const chunk = file.slice(start, end);
        const formData = new FormData();
        formData.append('file', chunk);
        formData.append('chunkNumber', chunkNumber.toString());
        formData.append('totalChunks', totalChunks.toString());
        formData.append('originalname', file.name);
        formData.append('model', model);
        for (const key in userCredentials) {
          formData.append(key, userCredentials[key]);
        }
        setIsLoading(true);
        setFilesData((prevfiles) =>
          prevfiles.map((curfile) => {
            if (curfile.name == file.name) {
              return {
                ...curfile,
                status: 'Uploading',
              };
            }
            return curfile;
          })
        );
        try {
          const apiResponse = await uploadAPI(chunk, model, chunkNumber, totalChunks, file.name);
          if (apiResponse?.status === 'Failed') {
            throw new Error(`message:${apiResponse.data.message},fileName:${apiResponse.data.file_name}`);
          } else {
            if (apiResponse.data) {
              setFilesData((prevfiles) =>
                prevfiles.map((curfile) => {
                  if (curfile.name == file.name) {
                    return {
                      ...curfile,
                      uploadProgress: Math.ceil(chunkNumber * chunkProgressIncrement),
                    };
                  }
                  return curfile;
                })
              );
            }
            setFilesData((prevfiles) =>
              prevfiles.map((curfile) => {
                if (curfile.name == file.name) {
                  return {
                    ...curfile,
                    uploadProgress: Math.ceil(chunkNumber * chunkProgressIncrement),
                  };
                }
                return curfile;
              })
            );
            chunkNumber++;
            start = end;
            if (start + chunkSize < file.size) {
              end = start + chunkSize;
            } else {
              end = file.size + 1;
            }
            uploadNextChunk();
          }
        } catch (error) {
          setIsLoading(false);
          if (error instanceof Error) {
            showErrorToast(`エラーが発生しました: ${error.message}`, true);
          }
          setFilesData((prevfiles) =>
            prevfiles.map((curfile) => {
              if (curfile.name == file.name) {
                return {
                  ...curfile,
                  status: 'Upload Failed',
                  type: `${file.name.substring(file.name.lastIndexOf('.') + 1, file.name.length).toUpperCase()}`,
                };
              }
              return curfile;
            })
          );
        }
      } else {
        setFilesData((prevfiles) =>
          prevfiles.map((curfile) => {
            if (curfile.name == file.name) {
              return {
                ...curfile,
                status: 'New',
                uploadProgress: 100,
                createdAt: new Date(),
              };
            }
            return curfile;
          })
        );
        setIsClicked(false);
        setIsLoading(false);
        showSuccessToast(`${file.name} をアップロードしました`);
      }
    };

    uploadNextChunk();
  };

  return (
    <>
      <SpotlightTarget
        id='dropzone'
        hasPulse={true}
        indicatorVariant='border'
        hasAnchorPortal={false}
        borderRadius={11}
      >
        <Dropzone
          loadingComponent={isLoading && <Loader title='アップロード中' />}
          isTesting={true}
          className='bg-none! dropzoneContainer'
          supportedFilesDescription={
            <Typography variant='body-small'>
              <Flex>
                <span>{buttonCaptions.dropzoneSpan}</span>
                <div className='align-self-center'>
                  <IconButtonWithToolTip
                    label='対応ファイル形式'
                    clean
                    text={
                      <Typography variant='body-small'>
                        <Flex gap='3' alignItems='flex-start'>
                          <span>Microsoft Office (.docx, .pptx, .xls, .xlsx)</span>
                          <span>PDF (.pdf)</span>
                          <span>画像 (.jpeg, .jpg, .png, .svg)</span>
                          <span>テキスト (.html, .txt , .md)</span>
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
              'application/pdf': ['.pdf'],
              'image/*': ['.jpeg', '.jpg', '.png', '.svg'],
              'text/html': ['.html'],
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
              'text/plain': ['.txt'],
              'application/vnd.ms-powerpoint': ['.pptx'],
              'application/vnd.ms-excel': ['.xls'],
              'text/markdown': ['.md'],
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
            },
            onDrop: (f: Partial<globalThis.File>[]) => {
              onDropHandler(f);
            },
            onDropRejected: (e) => {
              if (e.length) {
                showErrorToast('アップロードに失敗しました。サポートされていないファイル形式です');
              }
            },
          }}
        />
      </SpotlightTarget>
      <Flex flexDirection='row' justifyContent='flex-end' className='mt-2'>
        <input
          ref={folderInputRef}
          type='file'
          multiple
          style={{ display: 'none' }}
          onChange={onFolderSelected}
          {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
        />
        <Button size='small' fill='outlined' onClick={() => folderInputRef.current?.click()}>
          フォルダを選択
        </Button>
      </Flex>
      <ReplaceConfirmDialog
        open={pendingDrop !== null}
        names={pendingDrop?.replacing ?? []}
        hasNewFiles={(pendingDrop?.files.length ?? 0) > (pendingDrop?.replacing.length ?? 0)}
        onReplace={() => {
          const p = pendingDrop;
          setPendingDrop(null);
          if (p) {
            addDroppedFiles(p.files);
          }
        }}
        onSkip={() => {
          const p = pendingDrop;
          setPendingDrop(null);
          if (p) {
            addDroppedFiles(p.files.filter((x) => !p.replacing.includes(x.name as string)));
          }
        }}
        onCancel={() => setPendingDrop(null)}
      />
    </>
  );
};

export default DropZone;
