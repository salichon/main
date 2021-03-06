/***************************************************************************
 * Copyright (C) GFZ Potsdam                                               *
 * All rights reserved.                                                    *
 *                                                                         *
 * GNU Affero General Public License Usage                                 *
 * This file may be used under the terms of the GNU Affero                 *
 * Public License version 3.0 as published by the Free Software Foundation *
 * and appearing in the file LICENSE included in the packaging of this     *
 * file. Please review the following information to ensure the GNU Affero  *
 * Public License version 3.0 requirements will be met:                    *
 * https://www.gnu.org/licenses/agpl-3.0.html.                             *
 ***************************************************************************/


#ifndef __SYNC_STAXML_CONVERT2STAXML_H__
#define __SYNC_STAXML_CONVERT2STAXML_H__


#include "converter.h"

#include <seiscomp/datamodel/dataextent.h>

#include <list>
#include <map>
#include <string>


namespace Seiscomp {

namespace FDSNXML {

class FDSNStationXML;
class Network;
class Station;
class Channel;
class Response;

}


namespace DataModel {

class Object;
class Inventory;
class Station;
class SensorLocation;
class Stream;
class Sensor;
class Datalogger;
class ResponseFIR;
class ResponseIIR;
class ResponsePAZ;
class ResponseFAP;
class ResponsePolynomial;
class DataAvailability;

}


//! \brief Converter class for SC3 -> StationXML  that works on a
//! \brief StationXML message and merges all pushed inventories.
class Convert2FDSNStaXML : public Converter {
	// ------------------------------------------------------------------
	//  Xstruction
	// ------------------------------------------------------------------
	public:
		//! C'tor
		Convert2FDSNStaXML(FDSNXML::FDSNStationXML *msg);


	// ------------------------------------------------------------------
	//  Public interface
	// ------------------------------------------------------------------
	public:
		void setAvailability(const DataModel::DataAvailability *dataAvailability);
		bool push(const DataModel::Inventory *inv);


	// ------------------------------------------------------------------
	//  Private interface
	// ------------------------------------------------------------------
	private:
		bool process(FDSNXML::Network *, const DataModel::Station *);
		bool process(FDSNXML::Station *,
		             const DataModel::SensorLocation *,
		             const DataModel::Stream *);
		bool process(FDSNXML::Channel *,
		             const DataModel::Stream *,
		             const DataModel::Datalogger *);
		bool process(FDSNXML::Channel *,
		             const DataModel::Stream *,
		             const DataModel::Sensor *);

		const DataModel::Datalogger *findDatalogger(const std::string &publicID);
		const DataModel::Sensor *findSensor(const std::string &publicID);
		const DataModel::ResponseFIR *findFIR(const std::string &publicID);
		const DataModel::ResponseIIR *findIIR(const std::string &publicID);
		const DataModel::ResponsePAZ *findPAZ(const std::string &publicID);
		const DataModel::ResponseFAP *findFAP(const std::string &publicID);
		const DataModel::ResponsePolynomial *findPoly(const std::string &publicID);


	// ------------------------------------------------------------------
	//  Members
	// ------------------------------------------------------------------
	private:
		typedef std::map<std::string, const DataModel::Object*> ObjectLookup;
		typedef std::map<std::string, DataModel::DataExtentPtr> ExtentLookup;

		ObjectLookup                       _dataloggerLookup;
		ObjectLookup                       _sensorLookup;
		ObjectLookup                       _firLookup;
		ObjectLookup                       _iirLookup;
		ObjectLookup                       _pazLookup;
		ObjectLookup                       _fapLookup;
		ObjectLookup                       _polyLookup;
		ExtentLookup                       _dataAvailabilityLookup;

		FDSNXML::FDSNStationXML           *_msg;
		const DataModel::Inventory        *_inv;

};


}


#endif
