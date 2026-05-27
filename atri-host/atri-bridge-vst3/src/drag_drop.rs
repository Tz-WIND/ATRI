use std::path::{Path, PathBuf};

use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeDragPayload {
    files: Vec<PathBuf>,
}

impl BridgeDragPayload {
    pub fn from_export_path(path: impl AsRef<Path>) -> Result<Self, BridgeDragError> {
        let path = path.as_ref();
        if path.as_os_str().is_empty() {
            return Err(BridgeDragError::EmptyExportPath);
        }

        Ok(Self {
            files: vec![path.to_path_buf()],
        })
    }

    pub fn files(&self) -> &[PathBuf] {
        &self.files
    }
}

pub trait BridgeDragService {
    fn start_drag(&self, payload: BridgeDragPayload) -> Result<(), BridgeDragError>;
}

#[derive(Debug, Default)]
pub struct NativeBridgeDragService;

impl BridgeDragService for NativeBridgeDragService {
    fn start_drag(&self, payload: BridgeDragPayload) -> Result<(), BridgeDragError> {
        start_native_file_drag(&payload)
    }
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum BridgeDragError {
    #[error("export path is empty")]
    EmptyExportPath,
    #[error("there is no completed export file to drag")]
    MissingCompletedExport,
    #[error("drag-and-drop is only implemented for Windows HWND plug-in views")]
    UnsupportedPlatform,
    #[error("native drag-and-drop failed: {0}")]
    Native(String),
}

#[cfg(target_os = "windows")]
fn start_native_file_drag(payload: &BridgeDragPayload) -> Result<(), BridgeDragError> {
    windows_drag::start_file_drag(payload)
}

#[cfg(not(target_os = "windows"))]
fn start_native_file_drag(_payload: &BridgeDragPayload) -> Result<(), BridgeDragError> {
    Err(BridgeDragError::UnsupportedPlatform)
}

#[cfg(target_os = "windows")]
mod windows_drag {
    use super::{BridgeDragError, BridgeDragPayload};
    use std::mem::{ManuallyDrop, size_of};
    use std::os::windows::ffi::OsStrExt;
    use std::path::{Path, PathBuf};
    use std::ptr;

    use windows::Win32::Foundation::{
        BOOL, DRAGDROP_S_CANCEL, DRAGDROP_S_DROP, DRAGDROP_S_USEDEFAULTCURSORS, DV_E_DVASPECT,
        DV_E_FORMATETC, DV_E_LINDEX, DV_E_TYMED, E_NOTIMPL, E_POINTER, OLE_E_ADVISENOTSUPPORTED,
        RPC_E_CHANGED_MODE, S_OK,
    };
    use windows::Win32::System::Com::{
        DATADIR_GET, DVASPECT_CONTENT, FORMATETC, IAdviseSink, IDataObject, IDataObject_Impl,
        IEnumFORMATETC, IEnumSTATDATA, STGMEDIUM, STGMEDIUM_0, TYMED_HGLOBAL,
    };
    use windows::Win32::System::Memory::{GMEM_MOVEABLE, GlobalAlloc, GlobalLock, GlobalUnlock};
    use windows::Win32::System::Ole::{
        CF_HDROP, DROPEFFECT, DROPEFFECT_COPY, DoDragDrop, IDropSource, IDropSource_Impl,
        OleInitialize, OleUninitialize,
    };
    use windows::Win32::System::SystemServices::{MK_LBUTTON, MODIFIERKEYS_FLAGS};
    use windows::Win32::UI::Shell::SHCreateStdEnumFmtEtc;
    use windows::core::{Error as WinError, HRESULT, Result as WinResult, implement};

    pub fn start_file_drag(payload: &BridgeDragPayload) -> Result<(), BridgeDragError> {
        let files = resolve_drag_files(payload.files())?;
        let ole_initialized = initialize_ole()?;
        let data_object: IDataObject = FileDataObject::new(files).into();
        let drop_source: IDropSource = FileDropSource.into();
        let mut effect = DROPEFFECT(0);

        let result =
            unsafe { DoDragDrop(&data_object, &drop_source, DROPEFFECT_COPY, &mut effect) };

        if ole_initialized {
            unsafe {
                OleUninitialize();
            }
        }

        if result.is_ok() {
            Ok(())
        } else {
            Err(BridgeDragError::Native(format!("{result:?}")))
        }
    }

    #[cfg(test)]
    pub fn hdrop_wide_bytes_for_test(files: &[PathBuf]) -> Vec<u8> {
        hdrop_wide_bytes(files)
    }

    #[implement(IDataObject)]
    struct FileDataObject {
        files: Vec<PathBuf>,
    }

    impl FileDataObject {
        fn new(files: Vec<PathBuf>) -> Self {
            Self { files }
        }
    }

    #[allow(non_snake_case)]
    impl IDataObject_Impl for FileDataObject {
        fn GetData(&self, pformatetcin: *const FORMATETC) -> WinResult<STGMEDIUM> {
            let result = query_hdrop_format(pformatetcin);
            if result != S_OK {
                return Err(WinError::from(result));
            }

            let hglobal = hdrop_hglobal(&self.files)?;
            Ok(STGMEDIUM {
                tymed: TYMED_HGLOBAL.0 as u32,
                u: STGMEDIUM_0 { hGlobal: hglobal },
                pUnkForRelease: ManuallyDrop::new(None),
            })
        }

        fn GetDataHere(
            &self,
            _pformatetc: *const FORMATETC,
            _pmedium: *mut STGMEDIUM,
        ) -> WinResult<()> {
            Err(WinError::from(E_NOTIMPL))
        }

        fn QueryGetData(&self, pformatetc: *const FORMATETC) -> HRESULT {
            query_hdrop_format(pformatetc)
        }

        fn GetCanonicalFormatEtc(
            &self,
            _pformatectin: *const FORMATETC,
            _pformatetcout: *mut FORMATETC,
        ) -> HRESULT {
            E_NOTIMPL
        }

        fn SetData(
            &self,
            _pformatetc: *const FORMATETC,
            _pmedium: *const STGMEDIUM,
            _frelease: BOOL,
        ) -> WinResult<()> {
            Err(WinError::from(E_NOTIMPL))
        }

        fn EnumFormatEtc(&self, dwdirection: u32) -> WinResult<IEnumFORMATETC> {
            if dwdirection != DATADIR_GET.0 as u32 {
                return Err(WinError::from(E_NOTIMPL));
            }
            unsafe { SHCreateStdEnumFmtEtc(&[hdrop_format()]) }
        }

        fn DAdvise(
            &self,
            _pformatetc: *const FORMATETC,
            _advf: u32,
            _padvsink: Option<&IAdviseSink>,
        ) -> WinResult<u32> {
            Err(WinError::from(OLE_E_ADVISENOTSUPPORTED))
        }

        fn DUnadvise(&self, _dwconnection: u32) -> WinResult<()> {
            Err(WinError::from(OLE_E_ADVISENOTSUPPORTED))
        }

        fn EnumDAdvise(&self) -> WinResult<IEnumSTATDATA> {
            Err(WinError::from(OLE_E_ADVISENOTSUPPORTED))
        }
    }

    #[implement(IDropSource)]
    struct FileDropSource;

    #[allow(non_snake_case)]
    impl IDropSource_Impl for FileDropSource {
        fn QueryContinueDrag(
            &self,
            fescapepressed: BOOL,
            grfkeystate: MODIFIERKEYS_FLAGS,
        ) -> HRESULT {
            if fescapepressed.0 != 0 {
                return DRAGDROP_S_CANCEL;
            }
            if grfkeystate.0 & MK_LBUTTON.0 == 0 {
                return DRAGDROP_S_DROP;
            }
            S_OK
        }

        fn GiveFeedback(&self, _dweffect: DROPEFFECT) -> HRESULT {
            DRAGDROP_S_USEDEFAULTCURSORS
        }
    }

    fn resolve_drag_files(files: &[PathBuf]) -> Result<Vec<PathBuf>, BridgeDragError> {
        files
            .iter()
            .map(|file| {
                if file.is_absolute() {
                    Ok(file.clone())
                } else {
                    std::env::current_dir()
                        .map(|cwd| cwd.join(file))
                        .map_err(|err| BridgeDragError::Native(err.to_string()))
                }
            })
            .collect()
    }

    fn initialize_ole() -> Result<bool, BridgeDragError> {
        match unsafe { OleInitialize(None) } {
            Ok(()) => Ok(true),
            Err(error) if error.code() == RPC_E_CHANGED_MODE => Ok(false),
            Err(error) => Err(BridgeDragError::Native(error.to_string())),
        }
    }

    fn hdrop_format() -> FORMATETC {
        FORMATETC {
            cfFormat: CF_HDROP.0,
            ptd: ptr::null_mut(),
            dwAspect: DVASPECT_CONTENT.0,
            lindex: -1,
            tymed: TYMED_HGLOBAL.0 as u32,
        }
    }

    fn query_hdrop_format(format: *const FORMATETC) -> HRESULT {
        if format.is_null() {
            return E_POINTER;
        }

        let format = unsafe { &*format };
        if format.cfFormat != CF_HDROP.0 {
            return DV_E_FORMATETC;
        }
        if format.dwAspect != DVASPECT_CONTENT.0 {
            return DV_E_DVASPECT;
        }
        if format.lindex != -1 {
            return DV_E_LINDEX;
        }
        if format.tymed & TYMED_HGLOBAL.0 as u32 == 0 {
            return DV_E_TYMED;
        }
        S_OK
    }

    fn hdrop_hglobal(files: &[PathBuf]) -> WinResult<windows::Win32::Foundation::HGLOBAL> {
        let bytes = hdrop_wide_bytes(files);
        let hglobal = unsafe { GlobalAlloc(GMEM_MOVEABLE, bytes.len()) }?;
        let locked = unsafe { GlobalLock(hglobal) };
        if locked.is_null() {
            return Err(WinError::from_win32());
        }

        unsafe {
            ptr::copy_nonoverlapping(bytes.as_ptr(), locked.cast::<u8>(), bytes.len());
            let _ = GlobalUnlock(hglobal);
        }
        Ok(hglobal)
    }

    fn hdrop_wide_bytes(files: &[PathBuf]) -> Vec<u8> {
        const DROPFILES_HEADER_LEN: usize = 20;

        debug_assert_eq!(
            DROPFILES_HEADER_LEN,
            size_of::<windows::Win32::UI::Shell::DROPFILES>()
        );
        let mut wide_paths = Vec::<u16>::new();
        for file in files {
            wide_paths.extend(path_as_wide(file));
            wide_paths.push(0);
        }
        wide_paths.push(0);

        let mut bytes = vec![0_u8; DROPFILES_HEADER_LEN + wide_paths.len() * 2];
        bytes[0..4].copy_from_slice(&(DROPFILES_HEADER_LEN as u32).to_le_bytes());
        bytes[4..8].copy_from_slice(&0_i32.to_le_bytes());
        bytes[8..12].copy_from_slice(&0_i32.to_le_bytes());
        bytes[12..16].copy_from_slice(&0_i32.to_le_bytes());
        bytes[16..20].copy_from_slice(&1_i32.to_le_bytes());

        let mut cursor = DROPFILES_HEADER_LEN;
        for unit in wide_paths {
            bytes[cursor..cursor + 2].copy_from_slice(&unit.to_le_bytes());
            cursor += 2;
        }
        bytes
    }

    fn path_as_wide(path: &Path) -> Vec<u16> {
        path.as_os_str().encode_wide().collect()
    }
}

#[cfg(all(test, target_os = "windows"))]
mod tests {
    use std::path::PathBuf;

    #[test]
    fn hdrop_payload_encodes_wide_double_null_terminated_file_list() {
        let bytes = super::windows_drag::hdrop_wide_bytes_for_test(&[PathBuf::from(
            r"C:\ATRI\exports\session.dawproject",
        )]);

        assert_eq!(u32::from_le_bytes(bytes[0..4].try_into().unwrap()), 20);
        assert_eq!(i32::from_le_bytes(bytes[16..20].try_into().unwrap()), 1);
        assert!(bytes.ends_with(&[0, 0, 0, 0]));
    }
}
