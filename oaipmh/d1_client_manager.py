"""
________________________________________________________________________________________________________________________

d1_client_manager.py puts all the code managing GMN through API calls and the DataONE python library into one place. It
was written as part of an OAI-PMH based adapter, but can be incorporated into any adapter implementation.

________________________________________________________________________________________________________________________

"""

# Tested on dataone.libclient version 2.0.0

import datetime
import StringIO
import logging

# D1.
import d1_common.types.dataoneTypes_v2_0 as v2
import d1_common.const
import d1_client.mnclient_2_0
import d1_common.checksum


def _generate_version_pid(native_identifier):
    """This function is used by the D1ClientManager to generate a unique identifier for representing a record version
     in GMN. The identifier is derived from the record's identifier in the native repository by concatenating it with
     the datetime of loading the record. (Recall that the native repository's system identifier becomes the seriesId in
     GMN, and a new unique identifier is generated for every new record or new version of an existing record loaded 
     into GMN.)     
     """
    return native_identifier + datetime.datetime.now().strftime("_%Y%m%d_%H%M")


def _generate_system_metadata(scimeta_bytes, native_identifier_sid, version_pid, symeta_settings_dict):
    """This function generates a system metadata document for describing the science metadata record being loaded. Some
    of the fields, such as checksum and size, are based off the bytes of the science metadata object itself. Other
    system metadata fields are passed to D1ClientManager in a dict which is configured in the main adapter program."""
    sys_meta = v2.systemMetadata()
    sys_meta.seriesId = native_identifier_sid
    sys_meta.identifier = version_pid
    sys_meta.formatId = symeta_settings_dict['formatId']
    sys_meta.size = len(scimeta_bytes)
    sys_meta.checksum = \
        d1_common.checksum.create_checksum_object_from_stream(StringIO.StringIO(scimeta_bytes), algorithm='MD5')
    sys_meta.checksum.algorithm = 'MD5'
    sys_meta.dateUploaded = datetime.datetime.now()
    sys_meta.dateSysMetadataModified = datetime.datetime.now()
    sys_meta.rightsHolder = symeta_settings_dict['rightsholder']
    sys_meta.submitter = symeta_settings_dict['submitter']
    sys_meta.authoritativeMemberNode = symeta_settings_dict['authoritativeMN']
    sys_meta.originMemberNode = symeta_settings_dict['originMN']
    sys_meta.accessPolicy = _generate_public_access_policy()
    return sys_meta


def _generate_public_access_policy():
    """This function generates an access policy which is needed as part of system metadata for describing a science
    metadata object. In an adapter-based implementation, the ability to modify records is managed by the native
    repository, not GMN, and any changes in the native repository simple cascade down to GMN. This means it is 
     unnecessary to set specific access policies for individual records. Therefore, a generic public read-only access
      policy is generated and assigned as part of system metadata to every record as it is loaded."""
    accessPolicy = v2.AccessPolicy()
    accessRule = v2.AccessRule()
    accessRule.subject.append(d1_common.const.SUBJECT_PUBLIC)
    permission = v2.Permission('read')
    accessRule.permission.append(permission)
    accessPolicy.append(accessRule)
    return accessPolicy


class D1ClientManager:
    # Initialize the client manager with an instance of a member node client
    def __init__(self, gmn_baseurl, auth_cert, auth_cert_key, sysmeta_settings_dict):
        """
        :param gmn_baseurl: The base URL configured for the Generic Member Node installation.
        :param auth_cert: Certificate used for authenticating with the GMN server to make changes. If the adapter script
         is being run in standalone mode during development, then this will be the certificate generated by the GMN 
         server's local CA which was setup during installation of GMN. However, if this GMN instance has been registered
         with a DataONE Coordinating Node environment, then the certificate provided by DataONE should be used for
         authenticating with GMN.
        :param auth_cert_key: Also used for authentication. Similarly to the certificate described above, either a
         locally generated certificate key or a DataONE provided key will be used, depending on whether this node is
         still in development or is registered.
        :param sysmeta_settings_dict: System metadata settings which apply to every object loaded into GMN are 
         configured in the main script, and then passed within a dict to be used while creating and updating objects.
         """

        self.client = d1_client.mnclient_2_0.MemberNodeClient_2_0(
            gmn_baseurl,
            cert_path=auth_cert,
            key_path=auth_cert_key)
        self.sysmeta_settings_dict = sysmeta_settings_dict

    def check_if_identifier_exists(self, native_identifier_sid):
        """ 
        The main adapter script uses this function to determine if a science metadata record retrieved in an OAI-PMH
        harvest already exists in GMN. 

        :param native_identifier_sid: The native repository's system identifier for a record harvested in an OAI-PMH
         query, which is implemented as the DataONE seriesId.        

        :return: True if found or False if not.
        """
        try:
            self.client.getSystemMetadata(native_identifier_sid)
        except d1_common.types.exceptions.NotFound:
            return False
        else:
            return True

    def load_science_metadata(self, sci_metadata_bytes, native_identifier_sid):
        """
        Loads a new science metadata record into GMN using the .create() method from the Member Node API. 

        :param sci_metadata_bytes: The bytes of the science metadata record as a utf-encoded string.
        :param native_identifier_sid: The unique identifier of the metadata record in the native repository.

        """
        version_pid = _generate_version_pid(native_identifier_sid)
        system_metadata = _generate_system_metadata(sci_metadata_bytes, native_identifier_sid,
                                                    version_pid, self.sysmeta_settings_dict)
        try:
            self.client.create(version_pid, StringIO.StringIO(sci_metadata_bytes), system_metadata)
        except Exception, e:
            logging.error('Failed to create object with SID: ' + native_identifier_sid + ' / PID: ' + version_pid)
            logging.error(e)

    def update_science_metadata(self, sci_metadata_bytes, native_identifier_sid):
        """
        When a record is harvested from an OAI-PMH query whose native repository identifier already exists as a seriesId
        in GMN, then it is understood that the record has been modified in the native repository. The .update() API
        method is called to obsolete the old version of the science metadata in GMN, and load the changed record as a
        new object. The .update() method automates setting the obsoletes / obsoleted by properties of both old and new 
        objects in order to encode the relationship between the two, so there is no need to explicitly assign them.

        :param sci_metadata_bytes: The bytes of the new version of the science metadata record as a utf-encoded string.
        :param native_identifier_sid: The identifier of the record in its native repository which is implemented as the
         seriesId property in GMN.        
        """
        new_version_pid = _generate_version_pid(native_identifier_sid)
        old_version_system_metadata = self.client.getSystemMetadata(native_identifier_sid)
        old_version_pid = old_version_system_metadata.identifier.value()
        new_version_system_metadata = _generate_system_metadata(sci_metadata_bytes, native_identifier_sid,
                                                                new_version_pid, self.sysmeta_settings_dict)
        try:
            self.client.update(old_version_pid,
                               StringIO.StringIO(sci_metadata_bytes),
                               new_version_pid,
                               new_version_system_metadata)
        except Exception, e:
            logging.error('Failed to UPDATE object with SID: ' + native_identifier_sid + ' / PID: ' + old_version_pid)
            logging.error(e)

    def archive_science_metadata(self, current_version_pid):
        """
        This function is called by the main adapter script to archive an existing object in GMN.When GMN is first 
        populated, records which already have deleted status in the native repository will not be harvested from the 
        repository into GMN. By contrast, once a record has already been created into GMN, if it later becomes deleted,
        then the record will be archived in GMN.

        :param current_version_pid: The GMN unique identifier (pid) of the science metadata record to be archived.
        """
        try:
            self.client.archive(current_version_pid)
        except Exception, e:
            logging.error('Failed to ARCHIVE object PID: ' + current_version_pid)
            logging.error(e)
