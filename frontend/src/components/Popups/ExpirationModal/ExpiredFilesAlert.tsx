import { Checkbox, Flex, Typography } from '@neo4j-ndl/react';
import { DocumentTextIconOutline } from '@neo4j-ndl/react/icons';
import { LargefilesProps } from '../../../types';
import { List, ListItem, ListItemAvatar, ListItemButton, ListItemIcon, ListItemText } from '@mui/material';
import { FC } from 'react';
import BellImage from '../../../assets/images/Stopwatch-blue.svg';
import AlertIcon from '../../Layout/AlertIcon';
import { isExpired } from '../../../utils/Utils';
import { EXPIRATION_DAYS } from '../../../utils/Constants';
import { IconWithToolTip } from '../../UI/IconButtonToolTip';

const ExpiredFilesAlert: FC<LargefilesProps> = ({ Files, handleToggle, checked }) => {
  return (
    <div className='n-bg-palette-neutral-bg-weak p-4'>
      <div className='flex! flex-row pb-6 items-center mb-2'>
        <img
          style={{ width: 95, height: 95, marginRight: 10, alignSelf: 'flex-start' }}
          src={BellImage}
          alt='alert icon'
        />
        <div className='flex flex-col'>
          <Typography variant='h3'>ドキュメントの有効期限に関するお知らせ</Typography>
          <Typography variant='body-medium'>
            選択したドキュメントの中に、保存ポリシー(保存期間は{EXPIRATION_DAYS}日間)により有効期限が切れたものがあります。該当のドキュメントを削除して再アップロードしてください。
          </Typography>
          <List className='max-h-80 overflow-y-auto'>
            {Files.map((f) => {
              return (
                <ListItem key={f.name} disablePadding>
                  <ListItemButton role={undefined} dense>
                    <ListItemIcon>
                      <Checkbox
                        ariaLabel='selection checkbox'
                        isChecked={checked.includes(f.id)}
                        onChange={(e) => handleToggle(e.target.checked, f.id)}
                        htmlAttributes={{ tabIndex: -1 }}
                      />
                    </ListItemIcon>
                    <ListItemAvatar>
                      <DocumentTextIconOutline className='n-size-token-7 mr-2' />
                    </ListItemAvatar>
                    <ListItemText
                      primary={
                        <Flex flexDirection='row'>
                          <span className='word-break'>
                            {f.name} - {Math.floor((f?.size as number) / 1000)?.toFixed(2)}KB
                          </span>
                          {f.createdAt != undefined && isExpired(f.createdAt) ? (
                            <span>
                              <IconWithToolTip label={'期限切れ'} text={'ファイルの有効期限が切れています'} placement='top'>
                                <AlertIcon />
                              </IconWithToolTip>
                            </span>
                          ) : (
                            <></>
                          )}
                        </Flex>
                      }
                    />
                  </ListItemButton>
                </ListItem>
              );
            })}
          </List>
        </div>
      </div>
    </div>
  );
};
export default ExpiredFilesAlert;
