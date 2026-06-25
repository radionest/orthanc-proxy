import io

import pydicom
import requests
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid


def build_cyrillic_instance(study_uid, series_uid, sop_uid):
    ds = Dataset()
    ds.PatientName = "Иванов^Иван"
    ds.PatientID = "PROXY-TEST-1"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = CTImageStorage
    ds.Modality = "CT"
    ds.SpecificCharacterSet = "ISO_IR 192"
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = meta
    buf = io.BytesIO()
    try:
        pydicom.dcmwrite(buf, ds, enforce_file_format=True)  # pydicom >= 3.0
    except TypeError:
        ds.is_little_endian = True  # pydicom < 3.0 fallback
        ds.is_implicit_VR = False
        pydicom.dcmwrite(buf, ds, write_like_original=False)
    return buf.getvalue()


def upload_cyrillic_study(base_url):
    """Upload one CT instance to an Orthanc node. Returns the StudyInstanceUID."""
    study_uid, series_uid, sop_uid = generate_uid(), generate_uid(), generate_uid()
    dicom = build_cyrillic_instance(study_uid, series_uid, sop_uid)
    r = requests.post(base_url + "/instances", data=dicom, timeout=30)
    r.raise_for_status()
    return study_uid
