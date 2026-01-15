# A Collection of Python Utility Scripts/Tools

DOCX Duplicate Finder - (FindDuplicateDocs.py)

	This tool will search through a folder and subfolders and mas scan word documents.
	You can set a threshold for marking documents as duplicates.

	The output CSV report will list both files that match and display the exact duplicate match percent.


File compressor and encryptor (EncryptCompress)

	This tool will encrypt a file and compress it. You can set an encryption password.
	Useful for securely emailing files and for bypassing file type limitations on sharing or emailing platforms.


Image organizer with perceptual hashing and deduplication

	Scans a directory and all subfolders for image files
	Finds near-duplicate images using perceptual hashing, organizes by EXIF date
	Moves duplicates to separate folder, and writes an undo log and JSON report.
